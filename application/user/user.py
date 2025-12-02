from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from application.helpers import endpoint_helper, redis_helper, token_helpers
from application.algorithm import Algorithm
from application.auth import decode_token
from application.database import SessionLocal
from application import crud

router = APIRouter(
    prefix='',
    tags=['user']
)

FILE_NAME = "user:user"
handle_errors = endpoint_helper.handle_endpoint_errors(FILE_NAME)

@router.get('/')
async def root(): return RedirectResponse('/home')

@router.api_route('/home', methods=['POST', 'GET'])
@handle_errors
async def home(request: Request):
    data = decode_token(request)
    return {'status': 'OK', 'data': data}


@router.get("/res/{bakery_id}/{token_value}")
@handle_errors
async def queue_check(
    request: Request,
    bakery_id: int,
    token_value: str,
):
    """Public queue status endpoint that resolves the customer by daily token."""
    r = request.app.state.redis

    with SessionLocal() as db:
        customer = crud.get_customer_by_token_today(db, bakery_id, token_value)

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for token")

    t = customer.ticket_id

    # Redis keys
    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    name_key = redis_helper.REDIS_KEY_BREAD_NAMES
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    wait_list_key = redis_helper.REDIS_KEY_WAIT_LIST.format(bakery_id)
    served_key = redis_helper.REDIS_KEY_SERVED_TICKETS.format(bakery_id)
    user_current_ticket_key = redis_helper.REDIS_KEY_USER_CURRENT_TICKET.format(bakery_id)

    pipe = r.pipeline()
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.hgetall(name_key)
    pipe.hget(wait_list_key, t)
    pipe.sismember(served_key, t)
    pipe.get(user_current_ticket_key)
    time_per_bread_raw, reservations_map, bread_names_raw, wait_list_hit, is_served_flag, user_current_ticket_raw = await pipe.execute()

    bread_time = {int(k): int(v) for k, v in time_per_bread_raw.items()}
    reservation_dict = {
        int(k): [int(x) for x in v.split(',')] for k, v in reservations_map.items()
    }
    bread_names = {int(k): v for k, v in bread_names_raw.items()}

    if not bread_time:
        return {'msg': 'bakery does not exist or does not have any bread'}
    if not reservation_dict:
        return {'msg': 'queue is empty'}

    reservation_keys = sorted(reservation_dict.keys())
    algorithm_instance = Algorithm()

    current_ticket_id = None
    if user_current_ticket_raw is not None:
        try:
            current_ticket_id = int(user_current_ticket_raw)
        except (TypeError, ValueError):
            current_ticket_id = None

    is_user_exist = t in reservation_keys

    if not is_user_exist:
        in_wait_list = wait_list_hit is not None
        if in_wait_list:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": "ticket is in wait list",
                    "ticket_id": t,
                },
            )

        is_served = bool(is_served_flag)
        if is_served:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": "ticket is served",
                    "ticket_id": t,
                },
            )

        raise HTTPException(status_code=404, detail="Ticket does not Exist")

    reservation_number = t if is_user_exist else reservation_keys[-1]

    people_in_queue = sum(1 for key in reservation_keys if key < reservation_number)

    average_bread_time = sum(bread_time.values()) // len(bread_time)

    bread_ids_sorted = sorted(bread_time.keys())
    time_per_bread_list = [bread_time[k] for k in bread_ids_sorted]

    in_queue_customers_time = await algorithm_instance.calculate_in_queue_customers_time(
        reservation_keys,
        reservation_number,
        reservation_dict,
        time_per_bread_list,
        r=r,
        bakery_id=bakery_id
    )

    empty_slot_time = algorithm_instance.compute_empty_slot_time(
        reservation_keys,
        reservation_number,
        reservation_dict
    ) * average_bread_time

    user_breads_persian = user_breads = None
    if is_user_exist:

        user_breads_persian = {
            bread_names.get(bid, str(bid)): count
            for bid, count in zip(bread_ids_sorted, reservation_dict[reservation_number])
        }
        user_breads = {
            bid: count
            for bid, count in zip(bread_ids_sorted, reservation_dict[reservation_number])
        }

    ready, accurate_time, wait_until = await redis_helper.calculate_ready_status(
        r, bakery_id, user_breads, bread_time, reservation_keys, reservation_number, reservation_dict
    )

    return {
        "ready": ready,
        "accurate_time": accurate_time,
        "wait_until": wait_until,
        "people_in_queue": people_in_queue,
        "empty_slot_time_avg": empty_slot_time,
        "in_queue_customers_time": in_queue_customers_time,
        "user_breads": user_breads_persian,
        "current_ticket_id": current_ticket_id,
        "ticket_id": t
    }


@router.get("/queue_until_ticket_summary/{bakery_id}/{token_value}")
@handle_errors
async def queue_until_ticket_summary(
    request: Request,
    bakery_id: int,
    token_value: str,
):
    """Public endpoint: summary of queue up to and including ticket for a token."""
    r = request.app.state.redis

    with SessionLocal() as db:
        customer = crud.get_customer_by_token_today(db, bakery_id, token_value)

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for token")

    t = customer.ticket_id

    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    wait_list_key = redis_helper.REDIS_KEY_WAIT_LIST.format(bakery_id)
    served_key = redis_helper.REDIS_KEY_SERVED_TICKETS.format(bakery_id)

    pipe = r.pipeline()
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.hget(wait_list_key, t)
    pipe.sismember(served_key, t)
    time_per_bread_raw, reservations_map, wait_list_hit, is_served_flag = await pipe.execute()

    if not time_per_bread_raw:
        return {'msg': 'bakery does not exist or does not have any bread'}
    if not reservations_map:
        return {'msg': 'queue is empty'}

    reservation_dict = {
        int(k): [int(x) for x in v.split(',')] for k, v in reservations_map.items()
    }

    reservation_keys = sorted(reservation_dict.keys())

    if t not in reservation_keys:
        in_wait_list = wait_list_hit is not None
        if in_wait_list:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": "ticket is in wait list",
                    "ticket_id": t,
                },
            )

        is_served = bool(is_served_flag)
        if is_served:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": "ticket is served",
                    "ticket_id": t,
                },
            )

        raise HTTPException(status_code=404, detail="Ticket does not Exist")

    included_tickets = [key for key in reservation_keys if key <= t]

    people_in_queue_until_this_ticket = len(included_tickets)
    tickets_and_their_bread_count = {
        str(key): sum(reservation_dict[key]) for key in included_tickets
    }

    return {
        "people_in_queue_until_this_ticket": people_in_queue_until_this_ticket,
        "tickets_and_their_bread_count": tickets_and_their_bread_count,
    }
