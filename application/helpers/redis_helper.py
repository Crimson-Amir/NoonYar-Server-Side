from fastapi import HTTPException
from application import crud
from application.database import SessionLocal
from application.helpers.general_helpers import seconds_until_midnight_iran
import time
from collections import defaultdict


REDIS_KEY_PREFIX = "bakery:{0}"
REDIS_KEY_RESERVATIONS = f"{REDIS_KEY_PREFIX}:reservations"
REDIS_KEY_RESERVATION_ORDER = f"{REDIS_KEY_PREFIX}:reservation_order"
REDIS_KEY_TIME_PER_BREAD = f"{REDIS_KEY_PREFIX}:time_per_bread"
REDIS_KEY_WAIT_LIST = f"{REDIS_KEY_PREFIX}:wait_list"
REDIS_KEY_LAST_KEY = f"{REDIS_KEY_PREFIX}:last_ticket"
REDIS_KEY_UPCOMING_BREADS = f"{REDIS_KEY_PREFIX}:upcoming_breads"
REDIS_KEY_UPCOMING_CUSTOMERS = f"{REDIS_KEY_PREFIX}:upcoming_customers"
REDIS_KEY_CURRENT_UPCOMING_CUSTOMER = f"{REDIS_KEY_PREFIX}:current_upcoming_customer"
REDIS_KEY_BAKING_TIME_S = f"{REDIS_KEY_PREFIX}:baking_time_s"
REDIS_KEY_TIMEOUT_SEC = f"{REDIS_KEY_PREFIX}:timeout_sec"
REDIS_KEY_BREADS = f"{REDIS_KEY_PREFIX}:breads"
REDIS_KEY_LAST_BREAD_TIME = f"{REDIS_KEY_PREFIX}:last_bread_time"
REDIS_KEY_BREAD_TIME_DIFFS = f"{REDIS_KEY_PREFIX}:bread_time_diff"
REDIS_KEY_LAST_BREAD_INDEX = f"{REDIS_KEY_PREFIX}:last_bread_index"
REDIS_KEY_PREP_STATE = f"{REDIS_KEY_PREFIX}:prep_state"
REDIS_KEY_BREAD_NAMES = "bread_names"

async def handle_time_per_bread(r, bakery_id):
    time_per_bread = await get_bakery_time_per_bread(r, bakery_id, fetch_from_redis_first=False)
    if not time_per_bread:
        raise HTTPException(status_code=404, detail={"error": "empty bread type"})
    return time_per_bread


async def get_bakery_runtime_state(r, bakery_id):
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    upcoming_key = REDIS_KEY_UPCOMING_BREADS.format(bakery_id)

    pipe1 = r.pipeline()
    pipe1.hgetall(time_key)
    pipe1.hgetall(res_key)
    pipe1.smembers(upcoming_key)
    breads_type, reservation_dict, upcoming_members = await pipe1.execute()
    reservation_dict = {int(k): list(map(int, v.split(","))) for k, v in reservation_dict.items()} if reservation_dict else {}

    if not breads_type:
        breads_type = await handle_time_per_bread(r, bakery_id)

    upcoming_set = set(upcoming_members) if upcoming_members else None

    return breads_type, reservation_dict, upcoming_set

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

async def get_customer_ticket_data_pipe_without_reservations_with_upcoming_breads(r, bakery_id):
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    upcoming_key = REDIS_KEY_UPCOMING_BREADS.format(bakery_id)
    pipe1 = r.pipeline()
    pipe1.zrange(order_key, 0, 0)
    pipe1.hgetall(time_key)
    pipe1.smembers(upcoming_key)
    return await pipe1.execute()

async def get_customer_ticket_data_and_remove_skipped_ticket_pipe(r, bakery_id, customer_id):
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    skipped_key = REDIS_KEY_WAIT_LIST.format(bakery_id)
    upcomming_bread_key = REDIS_KEY_UPCOMING_BREADS.format(bakery_id)
    pipe1 = r.pipeline()
    pipe1.zrange(order_key, 0, 0)
    pipe1.hgetall(time_key)
    pipe1.hget(res_key, str(customer_id))
    pipe1.hget(skipped_key, str(customer_id))
    pipe1.hdel(skipped_key, str(customer_id))
    pipe1.smembers(upcomming_bread_key)
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
    local ttl = tonumber(ARGV[3])

    local ok = redis.call('HSETNX', reservations, ticket, value)
    if ok == 1 then
        redis.call('ZADD', order, score, ticket)
        redis.call('SET', last_ticket, ticket)
        -- set TTLs on all related keys
        redis.call('EXPIRE', reservations, ttl)
        redis.call('EXPIRE', order, ttl)
        redis.call('EXPIRE', last_ticket, ttl)
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
    ttl = seconds_until_midnight_iran()
    script = r.register_script(LUA_ADD_RESERVATION)
    result = await script(
        keys=[reservations_key, order_key, last_ticket_key],
        args=[str(customer_id), encoded, str(ttl)],
    )

    return result == 1

async def add_customer_to_wait_list(r, bakery_id: int, customer_id: int, reservations: list[int]=None, reservations_str=None):
    skipped_customer_key = REDIS_KEY_WAIT_LIST.format(bakery_id)
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
        bakery_breads = crud.get_active_bakery_breads(db, bakery_id)
        time_per_bread = {str(bread.bread_type_id): bread.preparation_time for bread in bakery_breads}

        pipe = r.pipeline()
        pipe.delete(time_key)
        if time_per_bread:
            pipe.hset(time_key, mapping=time_per_bread)
            ttl = seconds_until_midnight_iran()
            pipe.expire(time_key, ttl)
        
        await pipe.execute()
        return time_per_bread


async def reset_bread_names(r):
    bread_name_key = REDIS_KEY_BREAD_NAMES

    with SessionLocal() as db:
        breads = crud.get_active_breads(db)
        bread_names = {str(bread.bread_id): bread.name for bread in breads}

        pipe = r.pipeline()
        pipe.delete(bread_name_key)
        
        if bread_names:
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
        breads = crud.get_active_breads(db)
        bread_names = {str(bread.bread_id): bread.name for bread in breads}

        pipe = r.pipeline()
        pipe.delete(bread_name_key)
        
        if bread_names:
            pipe.hset(bread_name_key, mapping=bread_names)
            ttl = seconds_until_midnight_iran()
            pipe.expire(bread_name_key, ttl)
        
        await pipe.execute()
        return bread_names


async def get_bakery_wait_list(r, bakery_id, fetch_from_redis_first=True, bakery_time_per_bread=None):
    skipped_customer_key = REDIS_KEY_WAIT_LIST.format(bakery_id)
    if fetch_from_redis_first:
        reservations = await r.hgetall(skipped_customer_key)
        if reservations:
            return {int(k): list(map(int, v.split(","))) for k, v in reservations.items()}

    with SessionLocal() as db:
        today_customers = crud.get_today_wait_list(db, bakery_id)
        time_per_bread = bakery_time_per_bread or await get_bakery_time_per_bread(r, bakery_id)

        reservation_dict = {}
        pipe = r.pipeline()
        pipe.delete(skipped_customer_key)

        for customer in today_customers:
            bread_counts = {bread.bread_type_id: bread.count for bread in customer.bread_associations}
            reservation = [bread_counts.get(int(bid), 0) for bid in time_per_bread.keys()]
            reservation_dict[customer.ticket_id] = reservation

            pipe.hset(skipped_customer_key, str(customer.ticket_id), ",".join(map(str, reservation)))

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
            reservation_dict[customer.ticket_id] = reservation

            pipe.hset(reservations_key, str(customer.ticket_id), ",".join(map(str, reservation)))
            pipe.zadd(order_key, {str(customer.ticket_id): customer.ticket_id})

        
        if reservation_dict:
            ttl = seconds_until_midnight_iran()
            pipe.expire(reservations_key, ttl)
            pipe.expire(order_key, ttl)
        
        await pipe.execute()
        print("fetch reservation from db")

        return reservation_dict


async def get_bakery_time_per_bread(r, bakery_id: int, fetch_from_redis_first=True):
    """
    Fetch bread_type_id -> preparation_time mapping for a bakery.
    Stored in Redis as a HASH: HSET bakery:{id}:time_per_bread {bread_id} {time}
    """
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)

    if fetch_from_redis_first:
        raw = await r.hgetall(time_key)
        if raw:
            return {k: int(v) for k, v in raw.items()}

    with SessionLocal() as db:

        bakery_breads = crud.get_active_bakery_breads(db, bakery_id)
        time_per_bread = {str(bread.bread_type_id): int(bread.preparation_time) for bread in bakery_breads}
        pipe = r.pipeline()
        pipe.delete(time_key)

        if time_per_bread:
            pipe.hset(time_key, mapping=time_per_bread)
            ttl = seconds_until_midnight_iran()
            pipe.expire(time_key, ttl)
        
        await pipe.execute()
        print("fetch time per bread from db")
        return time_per_bread


def reset_time_per_bread_sync(r, db, bakery_id: int):
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    bakery_breads = crud.get_active_bakery_breads(db, bakery_id)
    time_per_bread = {str(bread.bread_type_id): bread.preparation_time for bread in bakery_breads}
    pipe = r.pipeline()
    pipe.delete(time_key)

    if time_per_bread:
        pipe.hset(time_key, mapping=time_per_bread)
        ttl = seconds_until_midnight_iran()
        pipe.expire(time_key, ttl)

    pipe.execute()
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

        last = last_customer.ticket_id if last_customer else 0

        pipe = r.pipeline()
        pipe.set(last_one_key, last)
        ttl = seconds_until_midnight_iran()
        pipe.expire(last_one_key, ttl)
        await pipe.execute()

        return last

async def is_ticket_in_wait_list(r, bakery_id, customer_id):
    skipped_list = REDIS_KEY_WAIT_LIST.format(bakery_id)
    is_exists = await r.hget(skipped_list, customer_id)
    return is_exists is not None


async def get_bakery_upcoming_breads(r, bakery_id: int, fetch_from_redis_first: bool = True) -> list[str]:
    key = REDIS_KEY_UPCOMING_BREADS.format(bakery_id)

    if fetch_from_redis_first:
        members = await r.smembers(key)
        if members:
            return list(members)

    with SessionLocal() as db:
        entries = crud.get_bakery_upcoming_breads(db, bakery_id)
        bread_ids = [str(e.bread_type_id) for e in entries]

    pipe = r.pipeline()
    pipe.delete(key)

    if bread_ids:
        pipe.sadd(key, *bread_ids)
        ttl = seconds_until_midnight_iran()
        pipe.expire(key, ttl)

    await pipe.execute()
    return bread_ids


async def add_upcoming_bread_to_bakery(r, bakery_id: int, bread_id: int):
    key = REDIS_KEY_UPCOMING_BREADS.format(bakery_id)
    pipe = r.pipeline()
    pipe.sadd(key, str(bread_id))
    ttl = seconds_until_midnight_iran()
    pipe.expire(key, ttl)
    await pipe.execute()


async def remove_upcoming_bread_from_bakery(r, bakery_id: int, bread_id: int):
    key = REDIS_KEY_UPCOMING_BREADS.format(bakery_id)
    pipe = r.pipeline()
    pipe.srem(key, str(bread_id))
    ttl = seconds_until_midnight_iran()
    pipe.expire(key, ttl)
    await pipe.execute()


async def ensure_upcoming_customers_zset(
        r,
        bakery_id: int,
        fetch_from_redis_first: bool = True
) -> list[int] | None:
    zkey = REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id)

    if fetch_from_redis_first:
        zcard = await r.zcard(zkey)
        if zcard and zcard > 0:
            members = await r.zrange(zkey, 0, -1)
            return [int(x) for x in members]

    pipe = r.pipeline()
    pipe.delete(zkey)

    with SessionLocal() as db:
        entries = crud.get_bakery_upcoming_customers(db, bakery_id)
        res = None
        if entries:
            customer_ids = {str(customer.customer.ticket_id): int(customer.customer.ticket_id) for customer in entries}
            pipe.zadd(zkey, customer_ids)
            ttl = seconds_until_midnight_iran()
            pipe.expire(zkey, ttl)
            res = customer_ids.values()

    await pipe.execute()
    return res

async def maybe_add_customer_to_upcoming_zset(
        r,
        bakery_id: int,
        customer_id: int,
        bread_requirements: dict[str, int],
        upcoming_members: set[str] | None = None
) -> bool:

    zkey = REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id)

    if upcoming_members is None:
        upcoming_members = await get_bakery_upcoming_breads(r, bakery_id)

    members = set(upcoming_members)

    if not members:
        return False
    will_add = any((bid in members and int(count) > 0) for bid, count in bread_requirements.items())
    if not will_add:
        return False

    pipe2 = r.pipeline()
    pipe2.zadd(zkey, {str(customer_id): int(customer_id)})
    ttl = seconds_until_midnight_iran()
    pipe2.expire(zkey, ttl)
    await pipe2.execute()
    return True
    
 

async def get_baking_time_s(r, bakery_id: int, fetch_from_redis_first: bool = True) -> int:
    key = REDIS_KEY_BAKING_TIME_S.format(bakery_id)
    if fetch_from_redis_first:
        val = await r.get(key)
        if val is not None:
            return int(val)

    pipe = r.pipeline()
    pipe.delete(key)

    with SessionLocal() as db:
        bakery = crud.get_bakery(db, bakery_id)
        value = bakery.baking_time_s
        pipe.set(key, value)
        
    ttl = seconds_until_midnight_iran()
    pipe.expire(key, ttl)
    await pipe.execute()
    
    return value


async def get_timeout_second(r, bakery_id: int, fetch_from_redis_first: bool = True) -> int:
    key = REDIS_KEY_TIMEOUT_SEC.format(bakery_id)
    if fetch_from_redis_first:
        val = await r.get(key)
        if val is not None:
            return int(val)

    pipe = r.pipeline()
    pipe.delete(key)

    with SessionLocal() as db:
        bakery = crud.get_bakery(db, bakery_id)
        value = None
        if bakery:
            value = bakery.timeout_sec
            pipe.set(key, value)
            ttl = seconds_until_midnight_iran()
            pipe.expire(key, ttl)

    await pipe.execute()
    return value

async def reset_timeout(r, bakery_id: int) -> int:
    key = REDIS_KEY_TIMEOUT_SEC.format(bakery_id)
    pipe = r.pipeline()
    pipe.delete(key)

    with SessionLocal() as db:
        res = crud.update_timeout_second(db, bakery_id, 0)
        value = None
        if res is not None:
            value = 0
            pipe.set(key, value)
            ttl = seconds_until_midnight_iran()
            pipe.expire(key, ttl)

    await pipe.execute()
    return value


async def update_timeout(r, bakery_id: int, new_timeout_second: int):
    key = REDIS_KEY_TIMEOUT_SEC.format(bakery_id)
    pipe = r.pipeline()
    pipe.set(key, new_timeout_second)
    ttl = seconds_until_midnight_iran()
    pipe.expire(key, ttl)
    await pipe.execute()


async def remove_customer_from_upcoming_customers(r, bakery_id, customer_id):
    zkey = REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id)
    await r.zrem(zkey, customer_id)

async def remove_customer_from_upcoming_customers_and_add_to_current_upcoming_customer(r, bakery_id, customer_id, preparation_time):
    cur_key = REDIS_KEY_CURRENT_UPCOMING_CUSTOMER.format(bakery_id)
    zkey = REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id)
    pipe = r.pipeline()
    pipe.setex(cur_key, preparation_time, customer_id)
    pipe.zrem(zkey, customer_id)
    await pipe.execute()

async def initialize_redis_sets(r, bakery_id: int):
    time_per_bread = await get_bakery_time_per_bread(r, bakery_id, fetch_from_redis_first=False)
    await get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=time_per_bread)
    await get_bakery_wait_list(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=time_per_bread)
    await get_last_ticket_number(r, bakery_id, fetch_from_redis_first=False)
    await get_bakery_upcoming_breads(r, bakery_id, fetch_from_redis_first=False)
    await ensure_upcoming_customers_zset(r, bakery_id, fetch_from_redis_first=False)
    await get_baking_time_s(r, bakery_id, fetch_from_redis_first=False)
    await get_timeout_second(r, bakery_id, fetch_from_redis_first=False)
    await load_breads_from_db(r, bakery_id)
    await rebuild_prep_state(r, bakery_id)

async def initialize_redis_sets_only_12_oclock(r, bakery_id: int):
    await reset_timeout(r, bakery_id)

async def purge_bakery_data(r, bakery_id: int):
    keys = [
        REDIS_KEY_RESERVATIONS.format(bakery_id),
        REDIS_KEY_RESERVATION_ORDER.format(bakery_id),
        REDIS_KEY_TIME_PER_BREAD.format(bakery_id),
        REDIS_KEY_WAIT_LIST.format(bakery_id),
        REDIS_KEY_LAST_KEY.format(bakery_id),
        REDIS_KEY_UPCOMING_BREADS.format(bakery_id),
        REDIS_KEY_UPCOMING_CUSTOMERS.format(bakery_id),
        REDIS_KEY_CURRENT_UPCOMING_CUSTOMER.format(bakery_id),
        REDIS_KEY_BAKING_TIME_S.format(bakery_id),
        REDIS_KEY_TIMEOUT_SEC.format(bakery_id),
        REDIS_KEY_PREP_STATE.format(bakery_id),
        REDIS_KEY_BREADS.format(bakery_id),
    ]

    pipe = r.pipeline(transaction=True)
    for key in keys:
        pipe.delete(key)
    await pipe.execute()


async def calculate_ready_status(
        r, bakery_id: int, current_user_detail: dict, time_per_bread: dict,
        reservation_keys: list, reservation_number: int, reservation_dict: dict
):
    breads_key = REDIS_KEY_BREADS.format(bakery_id)
    baking_time_key = REDIS_KEY_BAKING_TIME_S.format(bakery_id)

    people_before = [key for key in reservation_keys if key < reservation_number]
    breads_before = sum(sum(reservation_dict[k]) for k in people_before)

    now = time.time()
    bread_count = sum(current_user_detail.values())
    average_cook_time = sum(time_per_bread.values()) // len(time_per_bread)
    total_needed_breads = breads_before + bread_count

    # --- Single pipeline ---
    pipe = r.pipeline()
    pipe.get(baking_time_key)
    pipe.zrange(breads_key, 0, total_needed_breads - 1)
    baking_time_s_raw, zitems_raw = await pipe.execute()

    # Convert results
    baking_time_s = int(baking_time_s_raw) if baking_time_s_raw else 0
    zitems = [float(z) for z in zitems_raw]

    this_ticket_breads = zitems[breads_before : breads_before + bread_count]

    if not zitems:
        total_wait_s = baking_time_s
        for key in reservation_keys:
            if key > reservation_number:
                break

            breads_for_person = reservation_dict[key]
            total_wait_s += sum(
                count * time_per_bread[bread_id]
                for bread_id, count in zip(time_per_bread.keys(), breads_for_person)
            )
        return False, False, int(total_wait_s)

    if not this_ticket_breads:
        preparation_time_second = sum(
            count * time_per_bread[bread_id]
            for bread_id, count in current_user_detail.items()
        )

        total_remaining_before = 0
        total_breads_cooked = len(zitems)

        # iterate through people before this ticket
        for key in people_before:
            breads_for_person = reservation_dict[key]
            bread_ids = list(time_per_bread.keys())

            for bread_id, count in zip(bread_ids, breads_for_person):
                # Some breads already cooked
                if total_breads_cooked > 0:
                    if count <= total_breads_cooked:
                        total_breads_cooked -= count
                        continue
                    else:
                        remaining = count - total_breads_cooked
                        total_breads_cooked = 0
                        total_remaining_before += remaining * average_cook_time
                else:
                    total_remaining_before += count * time_per_bread[bread_id]

        total_wait_s = total_remaining_before + preparation_time_second + baking_time_s
        return False, False, int(total_wait_s)

    if bread_count > len(this_ticket_breads):
        breads_cook_time = [time_per_bread[bread_type] for bread_type, count in current_user_detail.items() if count > 0]
        this_ticket_average_cook_time = sum(breads_cook_time) // len(breads_cook_time)
        how_many_left = bread_count - len(this_ticket_breads)
        left_breads_second = how_many_left * this_ticket_average_cook_time
        return False, False, int(left_breads_second) + baking_time_s

    last_bread_time = this_ticket_breads[bread_count - 1]
    if now >= last_bread_time:
        return True, True, None
    else:
        return False, True, int(last_bread_time - now)


async def consume_ready_breads(r, bakery_id: int, bread_count: int):
    """
    Remove breads used for the current ticket from Redis.
    """
    breads_key = REDIS_KEY_BREADS.format(bakery_id)

    # Fetch first N breads (oldest / ready first)
    zitems = await r.zrange(breads_key, 0, bread_count - 1)
    if not zitems:
        return 0

    # Remove them from the sorted set
    removed_count = await r.zrem(breads_key, *zitems)
    return removed_count


async def load_breads_from_db(r, bakery_id: int):
    """
    Load today's breads from database into Redis on initialization
    """
    breads_key = REDIS_KEY_BREADS.format(bakery_id)

    with SessionLocal() as db:
        today_breads = crud.get_today_breads(db, bakery_id)

        if not today_breads:
            return

        pipe = r.pipeline()
        pipe.delete(breads_key)

        # Build mapping for zadd: {value: score}
        bread_mapping = {}
        for bread in today_breads:
            # Get hardware customer ID from internal customer ID
            customer = db.query(crud.models.Customer).filter_by(id=bread.belongs_to).first()
            if customer:
                baked_at_timestamp = int(bread.baked_at.timestamp())
                bread_value = f"{baked_at_timestamp}:{customer.ticket_id}"
                # Use bread.id as score (unique identifier)
                bread_mapping[bread_value] = bread.id

        if bread_mapping:
            pipe.zadd(breads_key, bread_mapping)
            ttl = seconds_until_midnight_iran()
            pipe.expire(breads_key, ttl)

        await pipe.execute()
        print(f"Loaded {len(bread_mapping)} breads from database for bakery {bakery_id}")


async def rebuild_prep_state(r, bakery_id: int):
    """
    Rebuild preparation state from loaded breads and reservations
    Should be called after load_breads_from_db
    """
    prep_state_key = REDIS_KEY_PREP_STATE.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    breads_key = REDIS_KEY_BREADS.format(bakery_id)

    pipe = r.pipeline()
    pipe.zrange(order_key, 0, -1)
    pipe.hgetall(res_key)
    pipe.zrangebyscore(breads_key, '-inf', '+inf')

    order_ids, reservations_map, all_breads = await pipe.execute()

    if not order_ids or not reservations_map:
        await r.delete(prep_state_key)
        return

    order_ids = [int(x) for x in order_ids]
    reservation_dict = {int(k): list(map(int, v.split(","))) for k, v in reservations_map.items()}

    # Count breads per customer
    breads_per_customer = defaultdict(int)
    for bread_value in all_breads:
        if ':' in bread_value:
            cid = int(bread_value.split(':')[1])
            breads_per_customer[cid] += 1

    # Find first incomplete customer
    for customer_id in order_ids:
        total_needed = sum(reservation_dict[customer_id])
        already_made = breads_per_customer.get(customer_id, 0)

        if already_made < total_needed:
            # This customer is still in preparation
            ttl = seconds_until_midnight_iran()
            await r.set(prep_state_key, f"{customer_id}:{already_made}", ex=ttl)
            print(
                f"Rebuilt prep_state for bakery {bakery_id}: customer {customer_id} has {already_made}/{total_needed} breads")
            return

    # All customers complete or no incomplete customers found
    await r.delete(prep_state_key)
    print(f"No incomplete customers found for bakery {bakery_id}")
