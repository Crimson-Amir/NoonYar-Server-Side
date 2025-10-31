from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Header, Request, Depends
from application.helpers.general_helpers import seconds_until_midnight_iran
from application.helpers import endpoint_helper, redis_helper, token_helpers
from application import tasks, algorithm, mqtt_client, crud, schemas
from application.logger_config import logger
from application.database import SessionLocal

FILE_NAME = "bakery:hardware_communication"
handle_errors = endpoint_helper.handle_endpoint_errors(FILE_NAME)

router = APIRouter(
    prefix='/hc',
    tags=['hardware_communication']
)

def validate_token(authorization: str = Header(...)) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=400, detail="Invalid or missing Authorization header")
    return authorization[len("Bearer "):]

@router.put('/new_ticket')
@handle_errors
async def new_ticket(
        request: Request,
        customer: schemas.NewCustomerRequirement,
        token: str = Depends(validate_token)
):
    bakery_id = customer.bakery_id

    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid bakery token")

    r = request.app.state.redis
    bread_requirements = customer.bread_requirements
    breads_type, reservation_dict, upcoming_set = await redis_helper.get_bakery_runtime_state(r, bakery_id)
    if breads_type.keys() != bread_requirements.keys():
        raise HTTPException(status_code=400, detail="Invalid bread types")

    if not reservation_dict:
        reservation_dict = await redis_helper.get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=breads_type)

    customer_ticket_id = await algorithm.Algorithm.new_reservation(reservation_dict, bread_requirements.values(), r, bakery_id)

    success = await redis_helper.add_customer_to_reservation_dict(
        r, customer.bakery_id, customer_ticket_id, bread_requirements, time_per_bread=breads_type
    )

    if not success:
        raise HTTPException(status_code=400, detail=f"Ticket {customer_ticket_id} already exists")

    customer_in_upcoming_customer = await redis_helper.maybe_add_customer_to_upcoming_zset(
        r, customer.bakery_id, customer_ticket_id, bread_requirements, upcoming_members=upcoming_set
    )

    if customer_in_upcoming_customer:
        await mqtt_client.update_has_upcoming_customer_in_queue(request, bakery_id)

    await mqtt_client.update_has_customer_in_queue(request, bakery_id)

    logger.info(f"{FILE_NAME}:new_cusomer", extra={"bakery_id": customer.bakery_id, "bread_requirements": bread_requirements, "customer_in_upcoming_customer": customer_in_upcoming_customer})
    tasks.register_new_customer.delay(customer_ticket_id, customer.bakery_id, bread_requirements, customer_in_upcoming_customer)

    return {
        'customer_ticket_id': customer_ticket_id,
        'customer_in_upcoming_customer': customer_in_upcoming_customer
    }


@router.put('/serve_ticket')
@handle_errors
async def serve_ticket(
        request: Request,
        ticket: schemas.TickeOperationtRequirement,
        token: str = Depends(validate_token)
):
    bakery_id = ticket.bakery_id

    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    customer_id = ticket.customer_ticket_id
    r = request.app.state.redis

    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    wait_list_key = redis_helper.REDIS_KEY_WAIT_LIST.format(bakery_id)
    pipe1 = r.pipeline()
    pipe1.hgetall(time_key)
    pipe1.hget(wait_list_key, str(customer_id))
    pipe1.hdel(wait_list_key, str(customer_id))

    time_per_bread, wait_list_reservations, remove_customer_from_wait_list= await pipe1.execute()

    if not remove_customer_from_wait_list or not wait_list_reservations:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Ticket is not in Wait list",
            }
        )

    tasks.serve_wait_list_ticket.delay(customer_id, bakery_id)
    customer_reservations = list(map(int, wait_list_reservations.split(",")))

    bread_ids = list(time_per_bread.keys())

    if len(customer_reservations) != len(bread_ids):
        raise HTTPException(status_code=404, detail="Reservation length mismatch with time_per_bread")

    user_detail = {bid: count for bid, count in zip(bread_ids, customer_reservations)}

    logger.info(f"{FILE_NAME}:serve_ticket", extra={
        "bakery_id": bakery_id,
        "customer_id": customer_id,
        "user_detail": user_detail,
    })

    return {
        "user_detail": user_detail,
    }


@router.get('/current_ticket/{bakery_id}')
@handle_errors
async def current_ticket(
        request: Request,
        bakery_id: int,
        token: str = Depends(validate_token)
):
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis

    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    pipe1 = r.pipeline()
    pipe1.zrange(order_key, 0, 0)
    pipe1.hgetall(time_key)
    pipe1.hgetall(res_key)
    current_ticket_id, time_per_bread, reservations_map = await pipe1.execute()

    if not current_ticket_id:
        await mqtt_client.update_has_customer_in_queue(request, bakery_id, False)
        return {"has_customer_in_queue": False}

    if not time_per_bread:
        raise HTTPException(status_code=404, detail={"error": "empty bread type"})

    if not reservations_map:
        raise HTTPException(status_code=404, detail={"error": "reservation is empty"})

    time_per_bread = {k: int(v) for k, v in time_per_bread.items()}
    current_ticket_id = int(current_ticket_id[0])
    reservation_dict = {
        int(k): [int(x) for x in v.split(',')] for k, v in reservations_map.items()
    }
    reservation_keys = sorted(reservation_dict.keys())
    bread_ids_sorted = sorted(time_per_bread.keys())

    user_breads = {bid: count for bid, count in zip(bread_ids_sorted, reservation_dict[current_ticket_id])}

    ready, _, wait_until = await redis_helper.calculate_ready_status(
        r, bakery_id, user_breads, time_per_bread, reservation_keys, current_ticket_id, reservation_dict
    )

    return {
        "ready": ready,
        "wait_until": wait_until,
        "has_customer_in_queue": True,
        "current_ticket_id": current_ticket_id,
        "current_user_detail": user_breads
    }


@router.put('/send_ticket_to_wait_list')
@handle_errors
async def send_ticket_to_wait_list(
        request: Request,
        ticket: schemas.TickeOperationtRequirement,
        token: str = Depends(validate_token)
):
    bakery_id = ticket.bakery_id


    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    customer_id = ticket.customer_ticket_id
    r = request.app.state.redis

    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)

    current_ticket_id_raw = await r.zrange(order_key, 0, 0)
    current_ticket_id = int(current_ticket_id_raw[0])

    if not current_ticket_id:
        raise HTTPException(status_code=404, detail={'status': 'The queue is empty'})

    await redis_helper.check_for_correct_current_id(customer_id, current_ticket_id)

    pipe = r.pipeline()
    pipe.hget(res_key, str(customer_id))
    pipe.hdel(res_key, customer_id)
    pipe.zrem(order_key, customer_id)
    current_customer_reservation, r1, r2 = await pipe.execute()

    if not bool(r1 and r2):
        reservation_list = await redis_helper.get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False)
        if not reservation_list:
            raise HTTPException(status_code=404, detail={'status': 'The queue is empty'})
        status, current_customer_reservation = await redis_helper.remove_customer_id_from_reservation(r, bakery_id, customer_id)
        if not status: raise HTTPException(status_code=401, detail="invalid customer_id")

    await redis_helper.add_customer_to_wait_list(r, bakery_id, customer_id, reservations_str=current_customer_reservation)

    next_ticket_id, time_per_bread, upcoming_breads = await redis_helper.get_customer_ticket_data_pipe_without_reservations_with_upcoming_breads(r, bakery_id)
    next_ticket_id = await redis_helper.check_current_ticket_id(r, bakery_id, next_ticket_id, return_error=False)
    next_user_detail = {}
    if next_ticket_id:
        customer_reservation = await redis_helper.get_customer_reservation(r, bakery_id, next_ticket_id)
        time_per_bread, customer_reservation = await redis_helper.get_current_cusomter_detail(r, bakery_id, next_ticket_id, time_per_bread, customer_reservation)
        next_user_detail = await redis_helper.get_customer_reservation_detail(time_per_bread, customer_reservation)

    tasks.send_ticket_to_wait_list.delay(customer_id, bakery_id)

    if any(bread in time_per_bread.keys() for bread in upcoming_breads):
        await redis_helper.remove_customer_from_upcoming_customers(r, bakery_id, customer_id)
        tasks.remove_customer_from_upcoming_customers.delay(customer_id, bakery_id)

    bread_count = sum([int(x) for x in current_customer_reservation.split(',')])
    removed = await redis_helper.consume_ready_breads(r, bakery_id, bread_count)
    logger.info(f"Removed {removed} breads for ticket {customer_id}")
    logger.info(f"{FILE_NAME}:send_ticket_to_wait_list", extra={"bakery_id": bakery_id, "customer_id": customer_id})
    return {
        "next_ticket_id": next_ticket_id,
        "next_user_detail": next_user_detail
    }


@router.get('/is_ticket_in_wait_list/{bakery_id}/{customer_id}')
@handle_errors
async def is_ticket_in_wait_list(
        request: Request,
        bakery_id: int,
        customer_id: int,
        token: str = Depends(validate_token)
):
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis
    status = await redis_helper.is_ticket_in_wait_list(r, bakery_id, customer_id)
    return {
        "is_ticket_in_wait_list": status
    }


@router.get('/upcoming/{bakery_id}')
@handle_errors
async def get_upcoming_customer(
        request: Request,
        bakery_id: int,
        token: str = Depends(validate_token)
):
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis

    cur_key = redis_helper.REDIS_KEY_CURRENT_UPCOMING_CUSTOMER.format(bakery_id)
    zkey = redis_helper.REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id)

    # Fetch both in one roundtrip
    pipe = r.pipeline()
    pipe.get(cur_key)
    pipe.zrange(zkey, 0, 0)
    cur_val, zmembers = await pipe.execute()

    if cur_val:
        customer_id = int(cur_val)
    elif zmembers:
        customer_id = int(zmembers[0])
    else:
        await mqtt_client.update_has_upcoming_customer_in_queue(request, bakery_id, False)
        return {"empty_upcoming": True}

    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    baking_time_key = redis_helper.REDIS_KEY_BAKING_TIME_S.format(bakery_id)
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    upcoming_breads_key = redis_helper.REDIS_KEY_UPCOMING_BREADS.format(bakery_id)

    pipe = r.pipeline()
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.get(baking_time_key)
    pipe.zrange(order_key, 0, -1)
    pipe.smembers(upcoming_breads_key)
    time_per_bread, reservations_map, baking_time_s_raw, order_ids, upcoming_breads = await pipe.execute()

    if time_per_bread:
        time_per_bread = {int(k): int(v) for k, v in time_per_bread.items()}

    if not time_per_bread or not order_ids:
        await mqtt_client.update_has_upcoming_customer_in_queue(request, bakery_id, False)
        return {"empty_upcoming": True}

    reservation_str = reservations_map.get(str(customer_id)) if reservations_map else None

    if not reservation_str:
        await mqtt_client.update_has_upcoming_customer_in_queue(request, bakery_id, False)
        return {"empty_upcoming": True}

    counts = [int(x) for x in reservation_str.split(',')]
    keys = [int(x) for x in order_ids]
    baking_time_s = int(baking_time_s_raw) if baking_time_s_raw else 0

    upcoming_breads_set = {int(x) for x in upcoming_breads}  # convert to int


    reservation_dict = {int(k): list(map(int, v.split(","))) for k, v in reservations_map.items()}

    sorted_keys = sorted(time_per_bread.keys())
    time_per_bread_list = [time_per_bread[k] for k in sorted_keys]
    alg = algorithm.Algorithm()
    max_bread_time = max(time_per_bread.values())
    in_queue_time = await alg.calculate_in_queue_customers_time(
        keys, customer_id, reservation_dict, time_per_bread_list, r=r, bakery_id=bakery_id
    )

    empty_slot_time = min(300, alg.compute_empty_slot_time(keys, customer_id, reservation_dict) * max_bread_time)
    delivery_time_s = in_queue_time + empty_slot_time
    preparation_time = alg.compute_bread_time(time_per_bread_list, counts)

    notification_lead_time_s = preparation_time + baking_time_s
    is_ready = delivery_time_s <= notification_lead_time_s

    response = {
        "empty_upcoming": False,
        "ready_to_show": False
    }

    if is_ready and cur_val is None:
        customer_breads = dict(zip(time_per_bread.keys(), counts))
        upcoming_customer_breads = {
            bread_id: qty
            for bread_id, qty in customer_breads.items()
            if bread_id in upcoming_breads_set
        }
        response['customer_id'] = customer_id
        response["breads"] = upcoming_customer_breads
        response['ready_to_show'] = True
        response['preparation_time'] = preparation_time

        await redis_helper.remove_customer_from_upcoming_customers_and_add_to_current_upcoming_customer(
            r, bakery_id, customer_id, preparation_time
        )
        tasks.remove_customer_from_upcoming_customers.delay(customer_id, bakery_id)

    return response


@router.post('/new_bread/{bakery_id}')
@handle_errors
async def new_bread(
        bakery_id,
        request: Request,
        token: str = Depends(validate_token)
):
    bakery_id = int(bakery_id)
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis

    # ============================================================
    # TRIP 1: Fetch ALL data in ONE pipeline (6-7 operations)
    # ============================================================
    prep_state_key = redis_helper.REDIS_KEY_PREP_STATE.format(bakery_id)
    baking_time_key = redis_helper.REDIS_KEY_BAKING_TIME_S.format(bakery_id)
    breads_key = redis_helper.REDIS_KEY_BREADS.format(bakery_id)
    last_bread_time_key = redis_helper.REDIS_KEY_LAST_BREAD_TIME.format(bakery_id)
    bread_diff_key = redis_helper.REDIS_KEY_BREAD_TIME_DIFFS.format(bakery_id)
    last_bread_index_key = redis_helper.REDIS_KEY_LAST_BREAD_INDEX.format(bakery_id)
    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)

    pipe = r.pipeline()
    pipe.get(prep_state_key)  # 0
    pipe.get(baking_time_key)  # 1
    pipe.get(last_bread_time_key)  # 2
    pipe.get(last_bread_index_key)  # 3
    pipe.hgetall(time_key)  # 4
    pipe.hgetall(res_key)  # 5
    pipe.zrange(order_key, 0, -1)  # 6

    results = await pipe.execute()
    prep_state_str, baking_time_s_raw, last_bread_time, last_bread_index, \
        time_per_bread, reservations_map, order_ids = results

    # ============================================================
    # PROCESS: All calculations in memory (no Redis calls)
    # ============================================================

    # Parse data
    order_ids = [int(x) for x in order_ids] if order_ids else []
    time_per_bread = {k: int(v) for k, v in time_per_bread.items()} if time_per_bread else {}
    reservations_map = reservations_map or {}

    if not order_ids or not reservations_map:
        return {"has_customer": False}

    # Calculate current prep state
    if prep_state_str:
        customer_id, bread_count = map(int, prep_state_str.split(':'))
    else:
        # Initialize with first customer
        customer_id = order_ids[0]
        bread_count = 0

    # Get customer reservation
    reservation_str = reservations_map.get(str(customer_id))
    if not reservation_str:
        return {"has_customer": False}

    bread_ids_sorted = sorted(time_per_bread.keys())
    counts = list(map(int, reservation_str.split(',')))
    total_needed = sum(counts)
    customer_breads = {bid: count for bid, count in zip(bread_ids_sorted, counts) if count > 0}

    # Calculate timing
    last_index = int(last_bread_index or 0)
    baking_time_s = int(baking_time_s_raw) if baking_time_s_raw else 0
    now = datetime.now()
    now_ts = int(now.timestamp())
    bread_cook_date = int((now + timedelta(seconds=baking_time_s)).timestamp())
    bread_index = last_index + 1
    ttl = seconds_until_midnight_iran()

    time_diff = None
    if last_bread_time:
        last_bread_time = int(float(last_bread_time))
        time_diff = now_ts - last_bread_time

    # Prepare bread value
    bread_value = f"{bread_cook_date}:{customer_id}"

    # Calculate next state
    bread_count += 1
    moved_to_next = False
    next_customer_id = customer_id
    next_customer_breads = customer_breads

    if bread_count >= total_needed:
        # Customer complete, find next
        moved_to_next = True
        try:
            current_idx = order_ids.index(customer_id)
            if current_idx + 1 < len(order_ids):
                next_customer_id = order_ids[current_idx + 1]
                next_reservation_str = reservations_map.get(str(next_customer_id))
                if next_reservation_str:
                    next_counts = list(map(int, next_reservation_str.split(',')))
                    next_customer_breads = {
                        bid: count
                        for bid, count in zip(bread_ids_sorted, next_counts)
                        if count > 0
                    }
                    new_prep_state = f"{next_customer_id}:0"
                else:
                    # No valid next customer
                    next_customer_id = None
                    new_prep_state = None
            else:
                # No more customers
                next_customer_id = None
                new_prep_state = None
        except ValueError:
            next_customer_id = None
            new_prep_state = None
    else:
        # Continue with same customer
        new_prep_state = f"{customer_id}:{bread_count}"

    # ============================================================
    # TRIP 2: Write ALL changes in ONE transaction
    # ============================================================
    pipe = r.pipeline(transaction=True)
    pipe.set(last_bread_time_key, now_ts, ex=ttl)
    pipe.zadd(breads_key, {bread_value: bread_index})
    pipe.expire(breads_key, ttl)
    pipe.set(last_bread_index_key, bread_index, ex=ttl)

    if time_diff is not None:
        pipe.zadd(bread_diff_key, {str(bread_index): time_diff})
        pipe.expire(bread_diff_key, ttl)

    if new_prep_state:
        pipe.set(prep_state_key, new_prep_state, ex=ttl)
    else:
        pipe.delete(prep_state_key)

    await pipe.execute()

    # ============================================================
    # ASYNC: Save to database (doesn't block response)
    # ============================================================
    tasks.save_bread_to_db.delay(customer_id, bakery_id, bread_cook_date)

    # ============================================================
    # LOGGING
    # ============================================================
    logger.info(
        f"{FILE_NAME}:new_bread",
        extra={
            "bakery_id": bakery_id,
            "bread_index": bread_index,
            "belongs_to": customer_id,
            "bread_count": bread_count,
            "total_needed": total_needed,
            "moved_to_next": moved_to_next,
        }
    )

    # ============================================================
    # RESPONSE
    # ============================================================
    if not next_customer_id:
        return {"has_customer": False}

    return {
        "customer_id": next_customer_id,
        "customer_breads": next_customer_breads,
        "next_customer": moved_to_next
    }


@router.get('/hardware_init')
@handle_errors
async def hardware_initialize(request: Request, bakery_id: int):
    db = SessionLocal()
    try:
        return await redis_helper.get_bakery_time_per_bread(request.app.state.redis, bakery_id)
    finally:
        db.close()


@router.put('/timeout/update')
@handle_errors
async def update_timeout(
        request: Request,
        data: schemas.UpdateTimeoutRequest,
        token: str = Depends(validate_token)
 ):
    bakery_id = data.bakery_id
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    with SessionLocal() as db:
        with db.begin():
            new_timeout = crud.update_timeout_second(db, bakery_id, data.seconds)
            if new_timeout is None:
                raise HTTPException(status_code=404, detail='Bakery not found')

    # Update Redis
    r = request.app.state.redis
    await redis_helper.update_timeout(r, bakery_id, new_timeout)

    logger.info(f"{FILE_NAME}:update_timeout", extra={"bakery_id": bakery_id, "timeout_min": new_timeout})
    return {"timeout_sec": new_timeout}

