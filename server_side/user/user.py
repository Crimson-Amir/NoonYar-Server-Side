from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import RedirectResponse

from algorithm import Algorithm
from private import SECRET_KEY
import jwt, algorithm

router = APIRouter(
    prefix='',
    tags=['user']
)

async def decode_access_token(request):
    data = request.cookies.get('access_token')
    if not data: return
    decode_data = jwt.decode(data, SECRET_KEY, algorithms=["HS256"])
    return decode_data

@router.get('/')
async def root(): return RedirectResponse('/home')

@router.api_route('/home', methods=['POST', 'GET'])
async def home(request: Request):
    data = await decode_access_token(request)
    return {'status': 'OK', 'data': data}

@router.get("/res/")
async def queue_check(request: Request, b: int, r: int):
    data = await decode_access_token(request)
    bakery_data = algorithm.get_bakery_data(b)

    reservation_dict = algorithm.get_bakery_reservations(request.state.redis, b)
    bread_time = algorithm.get_bakery_time_per_bread(request.state.redis, b)

    if not bread_time:
        return {'msg': 'bakery does not exist'}
    if not reservation_dict:
        return {'msg': 'queue is empty'}

    sorted_keys = sorted(reservation_dict)
    algorithm_instance = Algorithm()

    is_user_exist = r in reservation_dict
    reservation_number = r if is_user_exist else sorted_keys[-1]

    people_in_queue = sum(1 for key in sorted_keys if key < reservation_number)
    average_bread_time = sum(bread_time.values()) // len(bread_time)

    exist_customer_time = algorithm_instance.exist_customer_time(
        sorted_keys, reservation_number, reservation_dict, bread_time)

    empty_slot_time = algorithm_instance.compute_empty_slot_time(
        sorted_keys, reservation_number, reservation_dict) * average_bread_time

    return {
        "is_user_exist": is_user_exist,
        "people_in_queue": people_in_queue,
        "empty_slot_time": empty_slot_time,
        "exist_customer_time_s": exist_customer_time,
        "data": data
    }
