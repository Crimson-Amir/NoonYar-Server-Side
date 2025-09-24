from fastapi import APIRouter, HTTPException, Header, Request, Depends
from helpers import token_helpers, redis_helper, endpoint_helper
import schemas, tasks, algorithm, crud
from logger_config import logger
from database import SessionLocal

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

    # Add to upcoming customers ZSET if applicable
    customer_in_upcoming_customer = await redis_helper.maybe_add_customer_to_upcoming_zset(
        r, customer.bakery_id, customer_ticket_id, bread_requirements, upcoming_members=upcoming_set
    )

    logger.info(f"{FILE_NAME}:new_cusomer", extra={"bakery_id": customer.bakery_id, "bread_requirements": bread_requirements, "customer_in_upcoming_customer": customer_in_upcoming_customer})
    tasks.register_new_customer.delay(customer_ticket_id, customer.bakery_id, bread_requirements)

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

    current_ticket_id, time_per_bread, customer_reservation, skipped_customer_reservations, remove_skipped_customer = await redis_helper.get_customer_ticket_data_and_remove_skipped_ticket_pipe(r, bakery_id, customer_id)

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
            endpoint_helper.raise_empty_queue_exception()
        current_ticket_id, time_per_bread = await redis_helper.get_customer_ticket_data_pipe_without_reservations(r, bakery_id)

    current_ticket_id = await redis_helper.check_current_ticket_id(r, bakery_id, current_ticket_id)
    customer_reservation = await redis_helper.get_customer_reservation(r, bakery_id, current_ticket_id)
    time_per_bread, customer_reservations = await redis_helper.get_current_cusomter_detail(r, bakery_id, current_ticket_id, time_per_bread, customer_reservation)
    current_user_detail = await redis_helper.get_customer_reservation_detail(time_per_bread, customer_reservations)
    return {
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
    await redis_helper.remove_customer_id_from_reservation(r, bakery_id, customer_id)

    next_ticket_id, time_per_bread = await redis_helper.get_customer_ticket_data_pipe_without_reservations(r, bakery_id)
    next_ticket_id = await redis_helper.check_current_ticket_id(r, bakery_id, next_ticket_id, return_error=False)
    next_user_detail = {}
    if next_ticket_id:
        customer_reservation = await redis_helper.get_customer_reservation(r, bakery_id, next_ticket_id)
        time_per_bread, customer_reservation = await redis_helper.get_current_cusomter_detail(r, bakery_id, next_ticket_id, time_per_bread, customer_reservation)
        next_user_detail = await redis_helper.get_customer_reservation_detail(time_per_bread, customer_reservation)

    tasks.skip_customer.delay(customer_id, bakery_id)
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


@router.post('/upcoming')
@handle_errors
async def upcoming_notify_counts(
        data: schemas.UpcomingNotifyRequest,
        request: Request,
        token: str = Depends(validate_token)
):
    if not token_helpers.verify_bakery_token(token, data.bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis
    counts = await redis_helper.get_upcoming_notify_bread_counts(r, data.bakery_id, data.num_tickets)
    return counts


@router.get('/upcoming/first/{bakery_id}')
@handle_errors
async def get_upcoming_customer(
        request: Request,
        bakery_id: int,
        token: str = Depends(validate_token)
):
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")

    r = request.app.state.redis

    # Fetch earliest upcoming customer id
    zkey = redis_helper.REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id)
    members = await r.zrange(zkey, 0, 0)
    if not members:
        return {"empty_upcoming": True}
    customer_id = int(members[0])

    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    frt_key = redis_helper.REDIS_KEY_FULL_ROUND_TIME_MIN.format(bakery_id)
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)

    pipe = r.pipeline()
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.get(frt_key)
    pipe.zrange(order_key, 0, -1)
    time_per_bread, reservations_map, frt_min, order_ids = await pipe.execute()
    # Cast Redis hash values (strings) to ints for computation
    if time_per_bread:
        time_per_bread = {int(k): int(v) for k, v in time_per_bread.items()}

    if not time_per_bread or not order_ids:
        return {"empty_upcoming": True}

    reservation_str = reservations_map.get(str(customer_id)) if reservations_map else None
    if not reservation_str:
        return {"empty_upcoming": True}
    
    counts = [int(x) for x in reservation_str.split(',')]
    keys = [int(x) for x in order_ids]
    full_round_time_min = int(frt_min) if frt_min else 0

    reservation_dict = {int(k): [int(x) for x in v.split(',')] for k, v in (reservations_map or {}).items() if v}
    sorted_keys = sorted(time_per_bread.keys())
    time_per_bread_list = [time_per_bread[k] for k in sorted_keys]
    alg = algorithm.Algorithm()
    average_bread_time = sum(time_per_bread.values()) // len(time_per_bread)
    in_queue_time = await alg.calculate_in_queue_customers_time(
        keys, customer_id, reservation_dict, time_per_bread_list, r=r, bakery_id=bakery_id
    )

    empty_slot_time = min(300, alg.compute_empty_slot_time(keys, customer_id, reservation_dict)) * average_bread_time
    delivery_time_s = in_queue_time + empty_slot_time
    print(time_per_bread, counts)
    # Ensure deterministic order: sort by numeric bread id, then extract values

    cook_time_s = alg.compute_bread_time(time_per_bread_list, counts)

    full_round_time_s = full_round_time_min * 60

    notification_lead_time_s = cook_time_s + full_round_time_s
    
    is_ready = delivery_time_s <= notification_lead_time_s

    return {
        "empty_upcoming": False,
        "customer_id": customer_id,
        "in_queue_time_s": in_queue_time,
        "empty_slot_time_s": empty_slot_time,
        "delivery_time_s": delivery_time_s,
        "cook_time_s": cook_time_s,
        "notification_lead_time_s": notification_lead_time_s,
        "ready": is_ready
    }


@router.post('/timeout/update')
@handle_errors
async def update_timeout(
        request: Request,
        data: schemas.UpdateTimeoutRequest,
        token: str = Depends(validate_token)
 ):
    bakery_id = data.bakery_id
    if not token_helpers.verify_bakery_token(token, bakery_id):
        raise HTTPException(status_code=401, detail="Invalid token")
    db = SessionLocal()

    try:
        new_timeout = crud.update_timeout_min(db, bakery_id, data.minutes)
        if new_timeout is None:
            raise HTTPException(status_code=404, detail='Bakery not found')
    finally:
        db.close()

    # Update Redis
    r = request.app.state.redis
    key = redis_helper.REDIS_KEY_TIMEOUT_MIN.format(bakery_id)
    pipe = r.pipeline()
    pipe.set(key, new_timeout)
    ttl = redis_helper.seconds_until_midnight_iran()
    pipe.expire(key, ttl)
    await pipe.execute()

    logger.info(f"{FILE_NAME}:update_timeout", extra={"bakery_id": bakery_id, "timeout_min": new_timeout})
    return {"timeout_min": new_timeout}

@router.get('/hardware_init')
@handle_errors
async def hardware_initialize(request: Request, bakery_id: int):
    db = SessionLocal()
    try:
        return await redis_helper.get_bakery_time_per_bread(request.app.state.redis, bakery_id)
    finally:
        db.close()

