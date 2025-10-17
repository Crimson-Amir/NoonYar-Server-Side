from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import RedirectResponse
from application.helpers import endpoint_helper, redis_helper
from application.algorithm import Algorithm
from application.setting import settings
import jwt

router = APIRouter(
    prefix='',
    tags=['user']
)

FILE_NAME = "user:user"
handle_errors = endpoint_helper.handle_endpoint_errors(FILE_NAME)

async def decode_access_token(request):
    data = request.cookies.get('access_token')
    if not data: return
    decode_data = jwt.decode(data, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    return decode_data

@router.get('/')
async def root(): return RedirectResponse('/home')

@router.api_route('/home', methods=['POST', 'GET'])
@handle_errors
async def home(request: Request):
    data = await decode_access_token(request)
    return {'status': 'OK', 'data': data}


@router.get("/res/")
@handle_errors
async def queue_check(request: Request, b: int, t: int):
    """Check the queue status for a bakery and a target reservation number."""
    data = await decode_access_token(request)
    r = request.app.state.redis

    # Redis keys
    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(b)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(b)
    name_key = redis_helper.REDIS_KEY_BREAD_NAMES

    # Fetch all data in one pipeline
    pipe = r.pipeline()
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.hgetall(name_key)
    time_per_bread_raw, reservations_map, bread_names_raw = await pipe.execute()

    # Convert Redis byte/string values
    bread_time = {int(k): int(v) for k, v in time_per_bread_raw.items()}
    reservation_dict = {
        int(k): [int(x) for x in v.split(',')] for k, v in reservations_map.items()
    }
    bread_names = {int(k): v for k, v in bread_names_raw.items()}

    # Quick validation
    if not bread_time:
        return {'msg': 'bakery does not exist'}
    if not reservation_dict:
        return {'msg': 'queue is empty'}

    # Sort reservations (each key is a reservation number)
    reservation_keys = sorted(reservation_dict.keys())
    algorithm_instance = Algorithm()

    # Determine if target user exists
    is_user_exist = t in reservation_keys
    reservation_number = t if is_user_exist else reservation_keys[-1]

    # Count people before the user in queue
    people_in_queue = sum(1 for key in reservation_keys if key < reservation_number)

    # Compute average bread time
    average_bread_time = sum(bread_time.values()) // len(bread_time)

    # Prepare ordered list of time per bread (for algorithm)
    bread_ids_sorted = sorted(bread_time.keys())
    time_per_bread_list = [bread_time[k] for k in bread_ids_sorted]

    # Calculate total time for customers before the target one
    in_queue_customers_time = await algorithm_instance.calculate_in_queue_customers_time(
        reservation_keys,
        reservation_number,
        reservation_dict,
        time_per_bread_list,
        r=r,
        bakery_id=b
    )

    # Compute empty slot time (corrected: use reservation keys, not bread keys)
    empty_slot_time = algorithm_instance.compute_empty_slot_time(
        reservation_keys,
        reservation_number,
        reservation_dict
    ) * average_bread_time

    # Get user breads if exists
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
        r, b, user_breads, bread_time, reservation_keys, reservation_number, reservation_dict
    )

    # Return final response
    return {
        "ready": ready,
        "accurate_time": accurate_time,
        "wait_until": wait_until,
        "is_user_exists": is_user_exist,
        "people_in_queue": people_in_queue,
        "empty_slot_time_avg": empty_slot_time,
        "in_queue_customers_time": in_queue_customers_time,
        "user_breads": user_breads_persian,
        "data": data
    }
