from fastapi import HTTPException
import crud, private
from database import SessionLocal

REDIS_KEY_PREFIX = "bakery:{0}"
REDIS_KEY_RESERVATIONS = f"{REDIS_KEY_PREFIX}:reservations"
REDIS_KEY_RESERVATION_ORDER = f"{REDIS_KEY_PREFIX}:reservation_order"
REDIS_KEY_TIME_PER_BREAD = f"{REDIS_KEY_PREFIX}:time_per_bread"
REDIS_KEY_SKIPPED_CUSTOMER = f"{REDIS_KEY_PREFIX}:skipped_customer"
REDIS_KEY_BREAD_NAMES = "bread_names"


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
    reservation_dict = {int(k): list(map(int, v.split(","))) for k, v in
                        reservation_dict.items()} if reservation_dict else {}

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
            return first_id or []

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

async def get_customer_ticket_data_pipe(r, bakery_id, customer_id):
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    pipe1 = r.pipeline()
    pipe1.zrange(order_key, 0, 0)
    pipe1.hgetall(time_key)
    pipe1.hget(res_key, customer_id)
    return await pipe1.execute()

async def check_for_correct_current_id(r, bakery_id, customer_id, current_ticket_id):
    if not current_ticket_id:
        current_ticket_id = await get_order_set_from_reservations(r, bakery_id)
        if not current_ticket_id:
            raise HTTPException(status_code=404, detail={"error": "empty queue"})

    if current_ticket_id != customer_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid ticket number",
                "current_ticket_id": current_ticket_id,
            }
        )
    return current_ticket_id

async def get_current_cusomter_detail(r, bakery_id, customer_id, time_per_bread, customer_reservations):
    if not time_per_bread:
        time_per_bread = await handle_time_per_bread(r, bakery_id)

    if not customer_reservations:
        get_from_db = await get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False, bakery_time_per_bread=time_per_bread)
        reservation_dict = ",".join(map(str, get_from_db.get(int(customer_id), [])))
        if not reservation_dict:
            raise HTTPException(status_code=404, detail={"error": "reservation not found in list"})

    current_user_detail = await get_customer_reservation_detail(time_per_bread, customer_reservations)
    return current_user_detail

async def remove_customer_id_from_reservation(r, bakery_id, customer_id):
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    res_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    pipe2 = r.pipeline()
    pipe2.hdel(res_key, customer_id)
    pipe2.zrem(order_key, customer_id)
    return await pipe2.execute()

async def get_customer_reservation_detail(time_per_bread, reservations) -> dict[str, int] | None:
    counts = list(map(int, reservations.split(",")))
    bread_ids = list(time_per_bread.keys())

    if len(counts) != len(bread_ids):
        raise HTTPException(status_code=404, detail="Reservation length mismatch with time_per_bread")

    return {bid: count for bid, count in zip(bread_ids, counts)}


LUA_ADD_RESERVATION = """
    local reservations = KEYS[1]
    local order = KEYS[2]
    local ticket = ARGV[1]
    local value = ARGV[2]
    local score = tonumber(ARGV[1])

    -- Try insert
    local ok = redis.call('HSETNX', reservations, ticket, value)
    if ok == 1 then
        redis.call('ZADD', order, score, ticket)
    end
    return ok
"""


async def add_customer_to_reservation_dict(
        r, bakery_id: int, customer_id: int, bread_count_data: dict[str, int]
) -> bool:
    time_per_bread = await get_bakery_time_per_bread(r, bakery_id)
    reservations_key = REDIS_KEY_RESERVATIONS.format(bakery_id)
    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)

    reservation = [bread_count_data.get(bid, 0) for bid in time_per_bread.keys()]
    encoded = ",".join(map(str, reservation))

    script = r.register_script(LUA_ADD_RESERVATION)
    result = await script(
        keys=[reservations_key, order_key],
        args=[str(customer_id), encoded],
    )

    return result == 1


async def reset_bakery_metadata(r, bakery_id: int):
    """
    Refresh bread metadata into Redis as HASHES (better than JSON).
    """
    time_key = REDIS_KEY_TIME_PER_BREAD.format(bakery_id)

    db = SessionLocal()
    try:
        bakery_breads = crud.get_bakery_breads(db, bakery_id)
        time_per_bread = {str(bread.bread_type_id): bread.cook_time_s for bread in bakery_breads}

        if time_per_bread:
            await r.delete(time_key)
            await r.hset(time_key, mapping=time_per_bread)
        return time_per_bread
    finally:
        db.close()


async def reset_bread_names(r):
    """
    Refresh bread metadata into Redis as HASHES (better than JSON).
    """
    db = SessionLocal()
    try:
        breads = crud.get_breads(db)
        bread_names = {str(bread.bread_id): bread.name for bread in breads}

        if bread_names:
            await r.delete("bread_names")
            await r.hset(f"bread_names", mapping=bread_names)
        return bread_names
    finally:
        db.close()



async def get_bakery_bread_names(r):
    key = REDIS_KEY_BREAD_NAMES
    raw = await r.hgetall(key)
    if raw:
        return raw

    db = SessionLocal()
    try:
        breads = crud.get_breads(db)
        bread_names = {str(bread.bread_type_id): bread.name for bread in breads}

        if bread_names:
            await r.hset(key, mapping=bread_names)

        return bread_names
    finally:
        db.close()


async def get_bakery_skipped_customer(r, bakery_id):
    skipped_customer_key = REDIS_KEY_SKIPPED_CUSTOMER.format(bakery_id)
    reservations = await r.hgetall(skipped_customer_key)
    if reservations:
        return {int(k): list(map(int, v.split(","))) for k, v in reservations.items()} if reservations else {}

    db = SessionLocal()
    try:
        today_customers = crud.get_today_skipped_customers(db, bakery_id)
        time_per_bread = await get_bakery_time_per_bread(r, bakery_id)

        reservation_dict = {}
        pipe = r.pipeline()

        for customer in today_customers:
            bread_counts = {bread.bread_type_id: bread.count for bread in customer.bread_associations}
            reservation = [bread_counts.get(int(bid), 0) for bid in time_per_bread.keys()]
            reservation_dict[customer.hardware_customer_id] = reservation

            pipe.hset(skipped_customer_key, str(customer.hardware_customer_id), ",".join(map(str, reservation)))

        if reservation_dict:
            await pipe.execute()

        return reservation_dict
    finally:
        db.close()


async def get_bakery_reservations(r, bakery_id: int, fetch_from_redis_first=True, bakery_time_per_bread=None):
    reservations_key = REDIS_KEY_RESERVATIONS.format(bakery_id)

    if fetch_from_redis_first:
        reservations = await r.hgetall(reservations_key)
        if reservations:
            return {int(k): list(map(int, v.split(","))) for k, v in reservations.items()} if reservations else {}

    order_key = REDIS_KEY_RESERVATION_ORDER.format(bakery_id)
    db = SessionLocal()
    try:
        today_customers = crud.get_today_customers(db, bakery_id)
        time_per_bread = bakery_time_per_bread or await get_bakery_time_per_bread(r, bakery_id)

        reservation_dict = {}
        pipe = r.pipeline()
        pipe.delete(order_key)

        for customer in today_customers:
            bread_counts = {bread.bread_type_id: bread.count for bread in customer.bread_associations}
            reservation = [bread_counts.get(int(bid), 0) for bid in time_per_bread.keys()]
            reservation_dict[customer.hardware_customer_id] = reservation

            pipe.hset(reservations_key, str(customer.hardware_customer_id), ",".join(map(str, reservation)))
            pipe.zadd(order_key, {str(customer.hardware_customer_id): customer.hardware_customer_id})

        if reservation_dict:
            await pipe.execute()

        return reservation_dict
    finally:
        db.close()


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

    db = SessionLocal()
    try:
        bakery_breads = crud.get_bakery_breads(db, bakery_id)
        time_per_bread = {str(bread.bread_type_id): bread.cook_time_s for bread in bakery_breads}

        if time_per_bread:
            # Store in Redis hash
            await r.hset(time_key, mapping=time_per_bread)

        return time_per_bread
    finally:
        db.close()
