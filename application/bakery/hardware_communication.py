from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Header, Request, Depends

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

@router.put('/nc')
@handle_errors
async def new_customer(
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


@router.put('/nt')
@handle_errors
async def next_ticket(
        request: Request,
        ticket: schemas.TickeOperationtRequirement,
        token: str = Depends(validate_token)
):
    bakery_id = ticket.bakery_id

    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    customer_id = ticket.customer_ticket_id
    r = request.app.state.redis
    is_custome_skipped = False

    current_ticket_id, time_per_bread, customer_reservation, skipped_customer_reservations, remove_skipped_customer, upcoming_breads = await redis_helper.get_customer_ticket_data_and_remove_skipped_ticket_pipe(r, bakery_id, customer_id)

    if not current_ticket_id:
        reservation_list = await redis_helper.get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False)
        if not reservation_list and not remove_skipped_customer:
            endpoint_helper.raise_empty_queue_exception()
        current_ticket_id, time_per_bread, customer_reservation, *_ = await redis_helper.get_customer_ticket_data_and_remove_skipped_ticket_pipe(r, bakery_id, customer_id)

    if skipped_customer_reservations and remove_skipped_customer:
        customer_reservation = skipped_customer_reservations
        is_custome_skipped = True
        tasks.skipped_ticket_proccess.delay(customer_id, bakery_id)
    else:
        current_ticket_id = await redis_helper.check_current_ticket_id(r, bakery_id, current_ticket_id)
        await redis_helper.check_for_correct_current_id(customer_id, current_ticket_id)
        await redis_helper.remove_customer_id_from_reservation(r, bakery_id, customer_id)
        tasks.next_ticket_process.delay(customer_id, bakery_id)
        # TODO: IF YOU WANT REMOVE FROM TIHS LIST EVEN FOR SKIP TICKET, MOVE THIS SECTION DOWN
        if any(bread in time_per_bread.keys() for bread in upcoming_breads):
            await redis_helper.remove_customer_from_upcoming_customers(r, bakery_id, customer_id)
            tasks.remove_customer_from_upcoming_customers.delay(customer_id, bakery_id)

    time_per_bread, customer_reservation = await redis_helper.get_current_cusomter_detail(r, bakery_id, customer_id, time_per_bread, customer_reservation)
    current_user_detail = await redis_helper.get_customer_reservation_detail(time_per_bread, customer_reservation)

    logger.info(f"{FILE_NAME}:next_ticket", extra={
        "bakery_id": bakery_id,
        "customer_id": customer_id,
        "current_user_detail": current_user_detail,
        "is_custome_skipped": is_custome_skipped
    })

    return {
        "current_user_detail": current_user_detail,
        "skipped_customer": is_custome_skipped
    }


@router.get('/ct/{bakery_id}')
@handle_errors
async def current_ticket(
        request: Request,
        bakery_id: int,
        token: str = Depends(validate_token)
):
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis

    current_ticket_id, time_per_bread = await redis_helper.get_customer_ticket_data_pipe_without_reservations(r, bakery_id)
    if not current_ticket_id:
        reservation_list = await redis_helper.get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False)
        if not reservation_list:
            await mqtt_client.update_has_customer_in_queue(request, bakery_id, False)
            return {"has_customer_in_queue": False}
        current_ticket_id, time_per_bread = await redis_helper.get_customer_ticket_data_pipe_without_reservations(r, bakery_id)

    current_ticket_id = await redis_helper.check_current_ticket_id(r, bakery_id, current_ticket_id)
    customer_reservation = await redis_helper.get_customer_reservation(r, bakery_id, current_ticket_id)
    time_per_bread, customer_reservations = await redis_helper.get_current_cusomter_detail(r, bakery_id, current_ticket_id, time_per_bread, customer_reservation)
    current_user_detail = await redis_helper.get_customer_reservation_detail(time_per_bread, customer_reservations)
    return {
        "has_customer_in_queue": True,
        "current_ticket_id": current_ticket_id,
        "current_user_detail": current_user_detail
    }


@router.put('/st')
@handle_errors
async def skip_ticket(
        request: Request,
        ticket: schemas.TickeOperationtRequirement,
        token: str = Depends(validate_token)
):
    bakery_id = ticket.bakery_id


    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    customer_id = ticket.customer_ticket_id
    r = request.app.state.redis

    status, customer_reservation = await redis_helper.remove_customer_id_from_reservation(r, bakery_id, customer_id)
    if not status:
        reservation_list = await redis_helper.get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False)
        if not reservation_list:
            endpoint_helper.raise_empty_queue_exception()
        status, customer_reservation = await redis_helper.remove_customer_id_from_reservation(r, bakery_id, customer_id)
        if not status: raise HTTPException(status_code=401, detail="invalid customer_id")

    await redis_helper.add_customer_to_skipped_customers(r, bakery_id, customer_id, reservations_str=customer_reservation)

    next_ticket_id, time_per_bread, upcoming_breads = await redis_helper.get_customer_ticket_data_pipe_without_reservations_with_upcoming_breads(r, bakery_id)
    next_ticket_id = await redis_helper.check_current_ticket_id(r, bakery_id, next_ticket_id, return_error=False)
    next_user_detail = {}
    if next_ticket_id:
        customer_reservation = await redis_helper.get_customer_reservation(r, bakery_id, next_ticket_id)
        time_per_bread, customer_reservation = await redis_helper.get_current_cusomter_detail(r, bakery_id, next_ticket_id, time_per_bread, customer_reservation)
        next_user_detail = await redis_helper.get_customer_reservation_detail(time_per_bread, customer_reservation)

    tasks.skip_customer.delay(customer_id, bakery_id)

    if any(bread in time_per_bread.keys() for bread in upcoming_breads):
        await redis_helper.remove_customer_from_upcoming_customers(r, bakery_id, customer_id)
        tasks.remove_customer_from_upcoming_customers.delay(customer_id, bakery_id)

    logger.info(f"{FILE_NAME}:skip_ticket", extra={"bakery_id": bakery_id, "customer_id": customer_id})
    return {
        "next_ticket_id": next_ticket_id,
        "next_user_detail": next_user_detail
    }


@router.get('/is_ticket_in_skipped_list/{bakery_id}/{customer_id}')
@handle_errors
async def is_ticket_in_skipped_list(
        request: Request,
        bakery_id: int,
        customer_id: int,
        token: str = Depends(validate_token)
):
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis
    _is_ticket_in_skipped_list = await redis_helper.is_ticket_in_skipped_list(r, bakery_id, customer_id)
    return {
        "is_ticket_in_skipped_list": _is_ticket_in_skipped_list
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
    frt_key = redis_helper.REDIS_KEY_FULL_ROUND_TIME_MIN.format(bakery_id)
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    upcoming_breads_key = redis_helper.REDIS_KEY_UPCOMING_BREADS.format(bakery_id)

    pipe = r.pipeline()
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.get(frt_key)
    pipe.zrange(order_key, 0, -1)
    pipe.smembers(upcoming_breads_key)
    time_per_bread, reservations_map, frt_min, order_ids, upcoming_breads = await pipe.execute()

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
    full_round_time_min = int(frt_min) if frt_min else 0

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
    cook_time_s = alg.compute_bread_time(time_per_bread_list, counts)

    full_round_time_s = full_round_time_min * 60
    notification_lead_time_s = cook_time_s + full_round_time_s
    is_ready = delivery_time_s <= notification_lead_time_s

    response = {
        "empty_upcoming": False,
        "ready": False
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
        response['ready'] = True
        response['cook_time_s'] = cook_time_s

        await redis_helper.remove_customer_from_upcoming_customers_and_add_to_current_upcoming_customer(
            r, bakery_id, customer_id, cook_time_s
        )
        tasks.remove_customer_from_upcoming_customers.delay(customer_id, bakery_id)

    return response

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


@router.post('/new_bread/{bakery_id}')
@handle_errors
async def new_bread(
    bakery_id,
    request: Request,
    token: str = Depends(validate_token)
):
    bakery_id = bakery_id
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis

    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    frt_key = redis_helper.REDIS_KEY_FULL_ROUND_TIME_MIN.format(bakery_id)
    breads_key = redis_helper.REDIS_KEY_BREADS.format(bakery_id)
    last_bread_time_key = redis_helper.REDIS_KEY_LAST_BREAD_TIME.format(bakery_id)
    bread_diff_key = redis_helper.REDIS_KEY_BREAD_TIME_DIFFS.format(bakery_id)

    pipe = r.pipeline()
    pipe.hgetall(time_key)
    pipe.get(frt_key)
    pipe.zcard(breads_key)
    pipe.get(last_bread_time_key)
    time_per_bread, frt_min, bread_count, last_bread_t = await pipe.execute()

    if not time_per_bread or not frt_min:
        raise ValueError('time_per_bread or full round trip is empty')

    time_per_bread = {int(k): int(v) for k, v in time_per_bread.items()}
    avg_bread_time = sum(time_per_bread.values()) / len(time_per_bread)
    bread_count = int(bread_count or 0)

    now = datetime.now()
    now_ts = int(now.timestamp())
    bread_cook_date = int((now + timedelta(minutes=avg_bread_time + frt_min)).timestamp())
    bread_index = bread_count + 1

    time_diff = None
    if last_bread_t:
        last_bread_t = int(float(last_bread_t))
        time_diff = now_ts - last_bread_t

    pipe = r.pipeline(transaction=True)
    pipe.set(last_bread_time_key, now_ts)
    pipe.zadd(breads_key, {str(bread_cook_date): bread_index})

    if time_diff is not None:
        pipe.zadd(bread_diff_key, {str(time_diff): bread_index})

    await pipe.execute()

    logger.info(
        f"{FILE_NAME}:new_bread",
        extra={
            "bakery_id": bakery_id,
            "bread_index": bread_index,
            "bread_cook_date": bread_cook_date,
            "time_diff": time_diff,
        }
    )

    return {
        "bread_index": bread_index,
        "cook_date": bread_cook_date,
        "time_diff": time_diff,
    }

@router.get('/hardware_init')
@handle_errors
async def hardware_initialize(request: Request, bakery_id: int):
    db = SessionLocal()
    try:
        return await redis_helper.get_bakery_time_per_bread(request.app.state.redis, bakery_id)
    finally:
        db.close()

