from fastapi import APIRouter, Depends, HTTPException, Request
from application import crud, schemas
from application.helpers import redis_helper, endpoint_helper
from application.algorithm import Algorithm
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from application.logger_config import logger

FILE_NAME = 'admin:manage'
handle_errors = endpoint_helper.handle_endpoint_errors(FILE_NAME)

router = APIRouter(
    prefix='/admin',
    tags=['hardware_communication']
)

def require_admin(
    request: Request,
    db: Session = Depends(endpoint_helper.get_db)
):
    user = request.state.user
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    is_admin = crud.is_user_admin(db, user_id)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access only")

    return is_admin

@router.post('/new', response_model=schemas.NewAdminResult)
@handle_errors
async def new_admin(admin: schemas.NewAdminRequirement, db: Session = Depends(endpoint_helper.get_db), is_admin= Depends(require_admin)):
    try:
        new = crud.register_new_admin(db, admin.user_id, admin.status)
    except IntegrityError:
        # Likely foreign key violation: user_id does not exist in user_detail
        db.rollback()
        raise HTTPException(status_code=404, detail="User ID does not exist")

    logger.info(f"{FILE_NAME}:new_admin", extra={"user_id": admin.user_id, "status": admin.status, "add_by_admin_id": is_admin.admin_id})
    return new

@router.delete('/remove/{admin_id}')
@handle_errors
async def remove_admin(admin_id: int, db: Session = Depends(endpoint_helper.get_db), is_admin = Depends(require_admin)):
    result = crud.remove_admin(db, admin_id)
    if result:
        logger.info(f"{FILE_NAME}:remove_admin", extra={"admin_id": admin_id, "removed_by_admin_id": is_admin.admin_id})
        return {"status": "admin removed!"}

    raise HTTPException(
        status_code=404,
        detail="Admin ID does not exist"
    )

@router.get('/res_admin/{bakery_id}/{ticket_id}')
@handle_errors
async def res_admin_queue_status(
    request: Request,
    bakery_id: int,
    ticket_id: int,
    is_admin = Depends(require_admin),
):
    """Admin-only queue status for a bakery and a specific ticket_id.

    This mirrors the user-facing /res logic but is restricted to admins and
    remains keyed by raw ticket_id.
    """
    r = request.app.state.redis

    # Redis keys
    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(bakery_id)
    name_key = redis_helper.REDIS_KEY_BREAD_NAMES
    order_key = redis_helper.REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    wait_list_key = redis_helper.REDIS_KEY_WAIT_LIST.format(bakery_id)
    served_key = redis_helper.REDIS_KEY_SERVED_TICKETS.format(bakery_id)
    user_current_ticket_key = redis_helper.REDIS_KEY_USER_CURRENT_TICKET.format(bakery_id)

    # Fetch all data in one pipeline
    pipe = r.pipeline()
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.hgetall(name_key)
    pipe.hget(wait_list_key, ticket_id)
    pipe.sismember(served_key, ticket_id)
    pipe.get(user_current_ticket_key)
    time_per_bread_raw, reservations_map, bread_names_raw, wait_list_hit, is_served_flag, user_current_ticket_raw = await pipe.execute()

    # Convert Redis values
    bread_time = {int(k): int(v) for k, v in time_per_bread_raw.items()}
    reservation_dict = {
        int(k): [int(x) for x in v.split(',')] for k, v in reservations_map.items()
    }
    bread_names = {int(k): v for k, v in bread_names_raw.items()}

    # Quick validation
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

    is_user_exist = ticket_id in reservation_keys

    if not is_user_exist:
        in_wait_list = wait_list_hit is not None
        if in_wait_list:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": "ticket is in wait list",
                    "ticket_id": ticket_id,
                },
            )

        is_served = bool(is_served_flag)
        if is_served:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": "ticket is served",
                    "ticket_id": ticket_id,
                },
            )

        raise HTTPException(status_code=404, detail="Ticket does not Exist")

    reservation_number = ticket_id if is_user_exist else reservation_keys[-1]

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
        bakery_id=bakery_id,
    )

    empty_slot_time = algorithm_instance.compute_empty_slot_time(
        reservation_keys,
        reservation_number,
        reservation_dict,
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
    }