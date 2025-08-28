from fastapi import HTTPException
import crud, private
from database import SessionLocal
import heapq

async def get_order_set_from_reservations(r, bakery_id: int):
    reservations_key = f"bakery:{bakery_id}:reservations"
    order_key = f"bakery:{bakery_id}:reservation_order"

    hlen = await r.hlen(reservations_key)

    if hlen > 0:
        members = await r.hkeys(reservations_key)
        if members:
            mapping = {mid: int(mid) for mid in members}
            await r.zadd(order_key, mapping)
            lowest_two = heapq.nsmallest(2, mapping, key=mapping.get)
            return lowest_two or []


async def handle_time_per_bread(r, bakery_id):
    time_per_bread = await get_bakery_time_per_bread(r, bakery_id, fetch_from_redis_first=False)
    if not time_per_bread:
        raise HTTPException(status_code=404, detail={"error": "empty bread type"})
    return time_per_bread


async def fetch_metadata_and_reservations(r, bakery_id):
    time_key = f"bakery:{bakery_id}:time_per_bread"
    res_key = f"bakery:{bakery_id}:reservations"

    pipe1 = r.pipeline()
    pipe1.hgetall(time_key)
    pipe1.hgetall(res_key)
    breads_type, reservation_dict = await pipe1.execute()
    reservation_dict = {int(k): list(map(int, v.split(","))) for k, v in
                        reservation_dict.items()} if reservation_dict else {}

    if not breads_type:
        breads_type = await handle_time_per_bread(r, bakery_id)

    return breads_type, reservation_dict


async def _check_for_same_ticket_id(r, bakery_id, order_key, customer_id):
    first_two_ticket_id = await r.zrange(order_key, 0, 1)

    if not first_two_ticket_id:
        first_two_ticket_id = await get_order_set_from_reservations(r, bakery_id)

    current_ticket_id = int(first_two_ticket_id[0]) if first_two_ticket_id else None
    next_ticket_id = int(first_two_ticket_id[1]) if len(first_two_ticket_id or []) > 1 else None

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
    return next_ticket_id


async def next_ticket_opration(r, bakery_id, customer_id):
    time_key = f"bakery:{bakery_id}:time_per_bread"
    order_key = f"bakery:{bakery_id}:reservation_order"
    res_key = f"bakery:{bakery_id}:reservations"

    next_ticket_id = await _check_for_same_ticket_id(r, bakery_id, order_key, customer_id)

    pipe1 = r.pipeline()

    pipe1.hgetall(time_key)
    pipe1.hget(res_key, customer_id)
    if next_ticket_id is not None:
        pipe1.hget(res_key, next_ticket_id)
    pipe1.hdel(res_key, customer_id)
    pipe1.zrem(order_key, customer_id)

    time_per_bread, reservation_dict, *next_ticket_results, del1, del2 = await pipe1.execute()
    next_customer_reservations = next_ticket_results[0] if next_ticket_results else None

    if not time_per_bread:
        time_per_bread = await handle_time_per_bread(r, bakery_id)

    if not reservation_dict:
        get_from_db = await get_bakery_reservations(r, bakery_id, fetch_from_redis_first=False,
                                                    bakery_time_per_bread=time_per_bread)
        reservation_dict = ",".join(map(str, get_from_db.get(int(customer_id), [])))
        if not reservation_dict:
            raise HTTPException(status_code=404, detail={"error": "reservation not found in list"})
        next_customer_reservations = ",".join(map(str, get_from_db.get(next_ticket_id, [])))

        if not del1 and not del2:
            pipe2 = r.pipeline()
            pipe2.hdel(res_key, customer_id)
            pipe2.zrem(order_key, customer_id)
            await pipe2.execute()

    current_user_detail = await get_customer_reservation_detail(time_per_bread, reservation_dict)
    next_reservation_detail = await get_customer_reservation_detail(time_per_bread,
                                                                    next_customer_reservations) if next_customer_reservations else None

    return current_user_detail, next_ticket_id, next_reservation_detail

async def get_customer_reservation_detail(time_per_bread, reservations) -> dict[str, int] | None:
    counts = list(map(int, reservations.split(",")))
    bread_ids = list(time_per_bread.keys())

    if len(counts) != len(bread_ids):
        raise HTTPException(status_code=404, detail="Reservation length mismatch with time_per_bread")

    return {bid: count for bid, count in zip(bread_ids, counts)}


async def get_bakery_bread_names(r):
    key = "bread_names"
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
    reservations_key = f"bakery:{bakery_id}:reservations"
    order_key = f"bakery:{bakery_id}:reservation_order"

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
    db = SessionLocal()
    try:
        bakery_breads = crud.get_bakery_breads(db, bakery_id)
        time_per_bread = {str(bread.bread_type_id): bread.cook_time_s for bread in bakery_breads}

        if time_per_bread:
            await r.delete(f"bakery:{bakery_id}:time_per_bread")
            await r.hset(f"bakery:{bakery_id}:time_per_bread", mapping=time_per_bread)
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


async def get_bakery_reservations(r, bakery_id: int, fetch_from_redis_first=True, bakery_time_per_bread=None):
    reservations_key = f"bakery:{bakery_id}:reservations"

    if fetch_from_redis_first:
        reservations = await r.hgetall(reservations_key)
        if reservations:
            return {int(k): list(map(int, v.split(","))) for k, v in reservations.items()} if reservations else {}

    order_key = f"bakery:{bakery_id}:reservation_order"
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
        print(reservation_dict)

        return reservation_dict
    finally:
        db.close()


async def get_bakery_time_per_bread(r, bakery_id: int, fetch_from_redis_first=True):
    """
    Fetch bread_type_id -> cook_time_s mapping for a bakery.
    Stored in Redis as a HASH: HSET bakery:{id}:time_per_bread {bread_id} {time}
    """
    key = f"bakery:{bakery_id}:time_per_bread"

    if fetch_from_redis_first:
        raw = await r.hgetall(key)
        if raw:
            return {k: int(v) for k, v in raw.items()}

    db = SessionLocal()
    try:
        bakery_breads = crud.get_bakery_breads(db, bakery_id)
        time_per_bread = {str(bread.bread_type_id): bread.cook_time_s for bread in bakery_breads}

        if time_per_bread:
            # Store in Redis hash
            await r.hset(key, mapping=time_per_bread)

        return time_per_bread
    finally:
        db.close()



