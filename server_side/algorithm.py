import crud, private
from database import SessionLocal
import json
from typing import List, Dict

class Algorithm:

    @staticmethod
    def new_reservation(reservation_dict: Dict[int, List[int]], bread_counts: List[int]):
        """
        reservation_dict: {position: [bread_counts]}
        bread_counts: list of bread counts (any length)
        """
        total = sum(bread_counts)
        keys = sorted(reservation_dict.keys())

        if not keys:
            return 1

        last_key = keys[-1]
        last_sum = sum(reservation_dict[last_key])

        if total == 1:
            for i in range(len(keys) - 1):
                if sum(reservation_dict[keys[i]]) > 1 and sum(reservation_dict[keys[i + 1]]) > 1:
                    new_key = keys[i] + 1
                    while new_key in reservation_dict:
                        new_key += 1
                    return new_key

            new_key = 1 if not keys else keys[-1] + (2 if last_sum == 1 else 1)

        else:
            last_multiple = 0
            for key in reversed(keys):
                if sum(reservation_dict[key]) > 1:
                    last_multiple = key
                    break
            distance = (keys[-1] - last_multiple) // 2

            if last_multiple == last_key:
                new_key = last_key + 2
            elif distance < total and last_sum == 1:
                new_key = last_key + 1
            else:
                new_key = last_multiple + (total * 2)

        while new_key in reservation_dict:
            new_key += 1

        return new_key

    @staticmethod
    def compute_bread_time(time_per_bread, reserve):
        return sum(bread * time_per_bread.get(str(index), private.DEFAULT_BREAD_COOK_TIME_SECOND)
                   for index, bread in enumerate(reserve, start=1))

    def calculate_in_queue_customers_time(self, keys, index, reservation_dict, time_per_bread):
        return sum(
            self.compute_bread_time(time_per_bread, reservation_dict[key])
            for key in keys
            if key < index
        )

    @staticmethod
    def compute_empty_slot_time(keys, index, reservation_dict):
        consecutive_empty, consecutive_full = 0, 0

        prev_sum = sum(reservation_dict[keys[0]]) if keys else 0

        for i in range(1, len(keys)):
            curr_sum = sum(reservation_dict[keys[i]])

            if prev_sum == 1 and curr_sum == 1:
                if keys[i] <= index:
                    consecutive_empty += 1
            else:
                consecutive_empty = 0

            if prev_sum > 1 and curr_sum > 1:
                if keys[i] <= index:
                    consecutive_full += 1

            prev_sum = curr_sum

        return consecutive_empty + consecutive_full

async def _ensure_order_from_reservations(r, bakery_id: int) -> None:
    reservations_key = f"bakery:{bakery_id}:reservations"
    order_key = f"bakery:{bakery_id}:reservation_order"

    # If reservations exist but order is empty/missing, rebuild order from hash keys
    hlen = await r.hlen(reservations_key)
    zcard = await r.zcard(order_key)

    if hlen > 0 and zcard == 0:
        members = await r.hkeys(reservations_key)  # ["1","2","4",...]
        if members:
            mapping = {mid: int(mid) for mid in members}  # score = ticket_no
            await r.zadd(order_key, mapping)


async def get_current_ticket_id(r, bakery_id: int) -> int | None:
    order_key = f"bakery:{bakery_id}:reservation_order"

    ids = await r.zrange(order_key, 0, 0)
    if ids:
        return int(ids[0])

    await _ensure_order_from_reservations(r, bakery_id)
    ids = await r.zrange(order_key, 0, 0)
    if ids:
        return int(ids[0])

    return None

async def get_customer_reservation_detail(
    r, bakery_id: int, customer_id: int
) -> dict[str, int] | None:

    time_per_bread = await get_bakery_time_per_bread(r, bakery_id)
    if not time_per_bread:
        return None

    res_key = f"bakery:{bakery_id}:reservations"
    reservation_str = await r.hget(res_key, str(customer_id))
    if not reservation_str:
        return None

    counts = list(map(int, reservation_str.split(",")))
    bread_ids = list(time_per_bread.keys())

    if len(counts) != len(bread_ids):
        raise ValueError("Reservation length mismatch with time_per_bread")

    return {bid: count for bid, count in zip(bread_ids, counts)}


async def get_bakery_reservations(r, bakery_id: int):
    reservations_key = f"bakery:{bakery_id}:reservations"

    # Check if key exists at all
    if await r.exists(reservations_key):
        reservations = await r.hgetall(reservations_key)
        return {int(k): list(map(int, v.split(","))) for k, v in reservations.items()} if reservations else {}

    order_key = f"bakery:{bakery_id}:reservation_order"

    db = SessionLocal()
    try:
        today_customers = crud.get_today_customers(db, bakery_id)
        time_per_bread = await get_bakery_time_per_bread(r, bakery_id)

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

async def get_bakery_time_per_bread(r, bakery_id: int):
    """
    Fetch bread_type_id -> cook_time_s mapping for a bakery.
    Stored in Redis as a HASH: HSET bakery:{id}:time_per_bread {bread_id} {time}
    """
    key = f"bakery:{bakery_id}:time_per_bread"

    # Try Redis first
    raw = await r.hgetall(key)
    if raw:
        return {k: int(v) for k, v in raw.items()}

    # Fallback: fetch from DB
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


async def get_bakery_bread_names(r):
    key = "bread_names"
    raw = await r.hgetall(key)
    if raw:
        return raw  # already {str: str}

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

async def remove_customer_from_reservation_dict(pipe, bakery_id: int, customer_id: int):
    """
    Remove a single customer's reservation from Redis.
    """
    reservations_key = f"bakery:{bakery_id}:reservations"
    order_key = f"bakery:{bakery_id}:reservation_order"

    pipe.hdel(reservations_key, str(customer_id))
    pipe.zrem(order_key, str(customer_id))


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