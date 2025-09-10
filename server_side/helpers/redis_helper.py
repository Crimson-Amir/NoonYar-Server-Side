from fastapi import HTTPException
import crud, private
from database import SessionLocal
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

REDIS_KEY_PREFIX = "bakery:{0}"
REDIS_KEY_RESERVATIONS = f"{REDIS_KEY_PREFIX}:reservations"
REDIS_KEY_RESERVATION_ORDER = f"{REDIS_KEY_PREFIX}:reservation_order"
REDIS_KEY_TIME_PER_BREAD = f"{REDIS_KEY_PREFIX}:time_per_bread"
REDIS_KEY_SKIPPED_CUSTOMER = f"{REDIS_KEY_PREFIX}:skipped_customer"
REDIS_KEY_LAST_KEY = f"{REDIS_KEY_PREFIX}:last_ticket"
REDIS_KEY_BREAD_NAMES = "bread_names"

def seconds_until_midnight_iran():
    tz = ZoneInfo("Asia/Tehran")
    now = datetime.now(tz)
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int((midnight - now).total_seconds())

async def handle_time_per_bread(r, bakery_id):
    time_per_bread = await get_bakery_time_per_bread(r, bakery_id, fetch_from_redis_first=False)
    if not time_per_bread:
        raise HTTPException(status_code=404, detail={"error": "empty bread type"})
    return time_per_bread


async def fetch_metadata_and_reservations(r, bakery_id):
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)

    pipe1 = r.pipeline()
    pipe1.hgetall(time_key)
    pipe1.hgetall(res_key)
    breads_type, reservation_dict = await pipe1.execute()
    reservation_dict = {int(k): list(map(int, v.split(","))) for k, v in reservation_dict.items()} if reservation_dict else {}

    if not breads_type:
        breads_type = await handle_time_per_bread(r, bakery_id)

    return breads_type, reservation_dict

async def get_order_set_from_reservations(r, bakery_id: int):
    reservations_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)

    hlen = await r.hlen(reservations_key)

    if hlen > 0:
        members = await r.hkeys(reservations_key)
        if members:
            mapping = {mid: int(mid) for mid in members}
            await r.zadd(order_key, mapping)
            first_id = min(mapping, key=mapping.get)
            return list(first_id) or []

async def get_customer_reservation(r, bakery_id, customer_id):
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    return await r.hget(res_key, customer_id)


async def get_customer_ticket_data_pipe_without_reservations(r, bakery_id):
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    pipe1 = r.pipeline()
    pipe1.zrange(order_key, 0, 0)
    pipe1.hgetall(time_key)
    return await pipe1.execute()

async def get_customer_ticket_data_and_remove_skipped_ticket_pipe(r, bakery_id, customer_id):
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    skipped_key = REDIS_KEY_SKIPPED_CUSTOMER.format(bakery_id)
    pipe1 = r.pipeline()
    pipe1.zrange(order_key, 0, 0)
    pipe1.hgetall(time_key)
    pipe1.hget(res_key, str(customer_id))
    pipe1.hget(skipped_key, str(customer_id))
    pipe1.hdel(skipped_key, str(customer_id))
    return await pipe1.execute()

async def check_current_ticket_id(r, bakery_id, current_ticket_id: list, return_error=True):
    if not current_ticket_id:
        current_ticket_id = await get_order_set_from_reservations(r, bakery_id)
        if not current_ticket_id:
            if return_error:
                raise HTTPException(status_code=404, detail={"error": "empty queue"})
            else:
                return
    return int(current_ticket_id[0])

async def check_for_correct_current_id(customer_id, current_ticket_id):

    if current_ticket_id != customer_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid ticket number",
                "current_ticket_id": current_ticket_id,
            }
        )

async def get_current_cusomter_detail(r, bakery_id, customer_id, time_per_bread, customer_reservations):
    if not time_per_bread:
        time_per_bread = await handle_time_per_bread(r, bakery_id)

    if not customer_reservations:
        get_from_db = await get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=time_per_bread)
        customer_reservations = get_from_db.get(customer_id)
        if not customer_reservations: raise HTTPException(status_code=404, detail={"error": "reservation not found in list"})
    else:
        customer_reservations = list(map(int, customer_reservations.split(",")))

    return time_per_bread, customer_reservations

async def remove_customer_id_from_reservation(r, bakery_id, customer_id):
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    pipe = r.pipeline()
    pipe.hget(res_key, str(customer_id))
    pipe.hdel(res_key, customer_id)
    pipe.zrem(order_key, customer_id)
    reservations, r1, r2 = await pipe.execute()
    return bool(r1 and r2), reservations

async def get_customer_reservation_detail(time_per_bread, counts) -> dict[str, int] | None:
    bread_ids = list(time_per_bread.keys())

    if len(counts) != len(bread_ids):
        raise HTTPException(status_code=404, detail="Reservation length mismatch with time_per_bread")

    return {bid: count for bid, count in zip(bread_ids, counts)}


LUA_ADD_RESERVATION = """

    local reservations = KEYS[1]
    local order = KEYS[2]
    local last_ticket = KEYS[3]
    local ticket = ARGV[1]
    local value = ARGV[2]
    local score = tonumber(ARGV[1])
    
    local ok = redis.call('HSETNX', reservations, ticket, value)
    if ok == 1 then
        redis.call('ZADD', order, score, ticket)
        redis.call('SET', last_ticket, ticket)
    end
    
    return ok
"""


async def add_customer_to_reservation_dict(
        r, bakery_id: int, customer_id: int, bread_count_data: dict[str, int], time_per_bread=None
) -> bool:
    time_per_bread = time_per_bread or await get_bakery_time_per_bread(r, bakery_id)
    reservations_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    last_ticket_key = REDIS_KEY_LAST_KEY.format(bakery_id)

    reservation = [bread_count_data.get(bid, 0) for bid in time_per_bread.keys()]
    encoded = ",".join(map(str, reservation))

    script = r.register_script(LUA_ADD_RESERVATION)
    result = await script(
        keys=[reservations_key, order_key, last_ticket_key],
        args=[str(customer_id), encoded],
    )

    return result == 1

async def add_customer_to_skipped_customers(r, bakery_id: int, customer_id: int, reservations: list[int]=None, reservations_str=None):
    skipped_customer_key = REDIS_KEY_SKIPPED_CUSTOMER.format(bakery_id)
    pipe = r.pipeline()
    pipe.hset(skipped_customer_key, str(customer_id), reservations_str or ",".join(map(str, reservations)))
    ttl = seconds_until_midnight_iran()
    pipe.expire(skipped_customer_key, ttl)
    await pipe.execute()

async def reset_bakery_metadata(r, bakery_id: int):
    """
    Refresh bread metadata into Redis as HASHES (better than JSON).
    """
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)

    with SessionLocal() as db:
        bakery_breads = crud.get_bakery_breads(db, bakery_id)
        time_per_bread = {str(bread.bread_type_id): bread.cook_time_s for bread in bakery_breads}

        if time_per_bread:
            pipe = r.pipeline()
            pipe.delete(time_key)
            pipe.hset(time_key, mapping=time_per_bread)
            ttl = seconds_until_midnight_iran()
            pipe.expire(time_key, ttl)
            await pipe.execute()
        return time_per_bread


async def reset_bread_names(r):
    bread_name_key = REDIS_KEY_BREAD_NAMES

    with SessionLocal() as db:
        breads = crud.get_breads(db)
        bread_names = {str(bread.bread_id): bread.name for bread in breads}

        if bread_names:
            pipe = r.pipeline()
            pipe.delete(bread_name_key)
            pipe.hset(bread_name_key, mapping=bread_names)
            ttl = seconds_until_midnight_iran()
            pipe.expire(bread_name_key, ttl)
            await pipe.execute()
        return bread_names


async def get_bakery_bread_names(r):
    bread_name_key = REDIS_KEY_BREAD_NAMES
    raw = await r.hgetall(bread_name_key)
    if raw:
        return raw

    with SessionLocal() as db:
        breads = crud.get_breads(db)
        bread_names = {str(bread.bread_id): bread.name for bread in breads}

        if bread_names:
            pipe = r.pipeline()
            pipe.delete(bread_name_key)
            pipe.hset(bread_name_key, mapping=bread_names)
            ttl = seconds_until_midnight_iran()
            pipe.expire(bread_name_key, ttl)
            await pipe.execute()

        return bread_names


async def get_bakery_skipped_customer(r, bakery_id, fetch_from_redis_first=True, bakery_time_per_bread=None):
    skipped_customer_key = REDIS_KEY_SKIPPED_CUSTOMER.format(bakery_id)
    if fetch_from_redis_first:
        reservations = await r.hgetall(skipped_customer_key)
        if reservations:
            return {int(k): list(map(int, v.split(","))) for k, v in reservations.items()}

    with SessionLocal() as db:
        today_customers = crud.get_today_skipped_customers(db, bakery_id)
        time_per_bread = bakery_time_per_bread or await get_bakery_time_per_bread(r, bakery_id)

        reservation_dict = {}
        pipe = r.pipeline()
        pipe.delete(skipped_customer_key)

        for customer in today_customers:
            bread_counts = {bread.bread_type_id: bread.count for bread in customer.bread_associations}
            reservation = [bread_counts.get(int(bid), 0) for bid in time_per_bread.keys()]
            reservation_dict[customer.hardware_customer_id] = reservation

            pipe.hset(skipped_customer_key, str(customer.hardware_customer_id), ",".join(map(str, reservation)))

        if reservation_dict:
            ttl = seconds_until_midnight_iran()
            pipe.expire(skipped_customer_key, ttl)
            await pipe.execute()
            print("fetch skipped customer from db")

        return reservation_dict


async def get_bakery_reservations(r, bakery_id: int, fetch_from_redis_first=True, bakery_time_per_bread=None):
    reservations_key = REDIS_KEY_RESERVATIONS.format(bakery_id)

    if fetch_from_redis_first:
        reservations = await r.hgetall(reservations_key)
        if reservations:
            return {int(k): list(map(int, v.split(","))) for k, v in reservations.items()}

    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)

    with SessionLocal() as db:

        today_customers = crud.get_today_customers(db, bakery_id)
        time_per_bread = bakery_time_per_bread or await get_bakery_time_per_bread(r, bakery_id)
        reservation_dict = {}
        pipe = r.pipeline()
        pipe.delete(order_key)
        pipe.delete(reservations_key)

        for customer in today_customers:
            bread_counts = {bread.bread_type_id: bread.count for bread in customer.bread_associations}
            reservation = [bread_counts.get(int(bid), 0) for bid in time_per_bread.keys()]
            reservation_dict[customer.hardware_customer_id] = reservation

            pipe.hset(reservations_key, str(customer.hardware_customer_id), ",".join(map(str, reservation)))
            pipe.zadd(order_key, {str(customer.hardware_customer_id): customer.hardware_customer_id})

        if reservation_dict:
            ttl = seconds_until_midnight_iran()
            pipe.expire(reservations_key, ttl)
            pipe.expire(order_key, ttl)
            await pipe.execute()
            print("fetch reservation from db")

        return reservation_dict


async def get_bakery_time_per_bread(r, bakery_id: int, fetch_from_redis_first=True):
    """
    Fetch bread_type_id -> cook_time_s mapping for a bakery.
    Stored in Redis as a HASH: HSET bakery:{id}:time_per_bread {bread_id} {time}
    """
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)

    if fetch_from_redis_first:
        raw = await r.hgetall(time_key)
        if raw:
            return {k: int(v) for k, v in raw.items()}

    with SessionLocal() as db:

        bakery_breads = crud.get_bakery_breads(db, bakery_id)
        time_per_bread = {str(bread.bread_type_id): bread.cook_time_s for bread in bakery_breads}

        if time_per_bread:
            pipe = r.pipeline()
            pipe.delete(time_key)
            pipe.hset(time_key, mapping=time_per_bread)
            ttl = seconds_until_midnight_iran()
            pipe.expire(time_key, ttl)
            await pipe.execute()
            print("fetch time per bread from db")

        return time_per_bread


async def get_last_ticket_number(r, bakery_id, fetch_from_redis_first=True):
    last_one_key = REDIS_KEY_LAST_KEY.format(bakery_id)
    if fetch_from_redis_first:
        last_ticket_number = await r.get(last_one_key)
        if last_ticket_number:
            return int(last_ticket_number)

    with SessionLocal() as db:
        last_customer = crud.get_today_last_customer(db, bakery_id)

        last = last_customer.hardware_customer_id if last_customer else 1

        pipe = r.pipeline()
        pipe.set(last_one_key, last)
        ttl = seconds_until_midnight_iran()
        pipe.expire(last_one_key, ttl)
        await pipe.execute()

        return last

async def initialize_redis_sets(r, bakery_id: int):
    time_per_bread = await get_bakery_time_per_bread(r, bakery_id, fetch_from_redis_first=False)
    await get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=time_per_bread)
    await get_bakery_skipped_customer(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=time_per_bread)
    await get_last_ticket_number(r, bakery_id, fetch_from_redis_first=False)