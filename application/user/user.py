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
    data = await decode_access_token(request)
    r = request.app.state.redis

    time_key = redis_helper.REDIS_KEY_TIME_PER_BREAD.format(b)
    res_key = redis_helper.REDIS_KEY_RESERVATIONS.format(b)
    name_key = redis_helper.REDIS_KEY_BREAD_NAMES

    pipe = r.pipeline()
    pipe.hgetall(time_key)
    pipe.hgetall(res_key)
    pipe.hgetall(name_key)
    time_per_bread, reservations_map, bread_names = await pipe.execute()

    bread_time = {int(k): int(v) for k, v in time_per_bread.items()}
    reservation_dict = {int(k): v for k, v in reservations_map.items()}

    if not bread_time:
        return {'msg': 'bakery does not exist'}
    if not reservation_dict:
        return {'msg': 'queue is empty'}

    sorted_keys = sorted(reservation_dict.keys())
    algorithm_instance = Algorithm()

    is_user_exist = t in sorted_keys
    reservation_number = t if is_user_exist else sorted_keys[-1]
    people_in_queue = sum(1 for key in sorted_keys if key < reservation_number)
    average_bread_time = sum(bread_time.values()) // len(bread_time)
    sorted_keys = sorted(bread_time.keys())
    time_per_bread_list = [bread_time[k] for k in sorted_keys]

    in_queue_customers_time = await algorithm_instance.calculate_in_queue_customers_time(
        sorted_keys, reservation_number, reservation_dict, time_per_bread_list, r=request.app.state.redis, bakery_id=b)

    empty_slot_time = algorithm_instance.compute_empty_slot_time(
        sorted_keys, reservation_number, reservation_dict) * average_bread_time
    
    bread_ids = bread_time.keys()
    user_breads = {
        bread_names.get(bid, bid): count for bid, count in zip(bread_ids, reservation_dict.get(reservation_number, []))} if is_user_exist else None

    return {
        "is_user_exists": is_user_exist,
        "people_in_queue": people_in_queue,
        "empty_slot_time_avg": empty_slot_time,
        "in_queue_customers_time": in_queue_customers_time,
        "user_breads": user_breads,
        "data": data
    }
