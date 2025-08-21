import crud
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
                    return keys[i] + 1

            new_key = 1 if not keys else keys[-1] + (2 if last_sum == 1 else 1)
            return new_key

        else:
            last_multiple = 0
            for key in reversed(keys):
                if sum(reservation_dict[key]) > 1:
                    last_multiple = key
                    break
            distance = (keys[-1] - last_multiple) // 2

            if last_multiple == last_key:
                return last_key + 2
            elif distance < total and last_sum == 1:
                return last_key + 1
            else:
                return last_multiple + (total * 2)

    @staticmethod
    def compute_bread_time(time_per_bread, reserve):
        return sum(bread * time_per_bread.get(index, 1) for index, bread in enumerate(reserve))

    def exist_customer_time(self, keys, index, reservation_dict, time_per_bread):
        return sum(
            self.compute_bread_time(time_per_bread, reservation_dict[key])
            for key in keys
            if key < index
        )

    @staticmethod
    def compute_empty_slot_time(keys, index, reservation_dict):
        consecutive_empty, consecutive_full = 0, 0

        prev_sum = sum(reservation_dict[keys[0]]) if keys else 0  # Handle empty keys case

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

async def get_bakery_reservations(r, bakery_id: int):
    """
    Fetch reservations for a bakery.
    Structure: {customer_id: [bread_counts]}
    """
    reservations_key = f"bakery:{bakery_id}:reservations"
    order_key = f"bakery:{bakery_id}:reservation_order"

    reservations = await r.hgetall(reservations_key)

    if reservations:
        return {int(k): list(map(int, v.split(","))) for k, v in reservations.items()}

    db = SessionLocal()
    try:
        today_customers = crud.get_today_customers(db, bakery_id)
        time_per_bread = await get_bakery_time_per_bread(r, bakery_id)

        reservation_dict = {}
        pipe = r.pipeline()

        for customer in today_customers:
            bread_counts = {bread.bread_type_id: bread.count for bread in customer.bread_associations}
            reservation = [bread_counts.get(int(bid), 0) for bid in time_per_bread.keys()]
            reservation_dict[customer.id] = reservation

            pipe.hset(reservations_key, str(customer.id), ",".join(map(str, reservation)))
            pipe.zadd(order_key, {str(customer.id): customer.id})

        if reservation_dict:
            await pipe.execute()

        return reservation_dict
    finally:
        db.close()


async def get_bakery_time_per_bread(r, bakery_id: int):
    """
    Fetch bread type -> cook time mapping for a bakery.
    Stored in Redis as JSON string: {"1": 60, "2": 80, "3": 20}
    """
    key = f"bakery:{bakery_id}:time_per_bread"
    raw = await r.get(key)

    if raw:
        return json.loads(raw)  # Already sorted because DB query is sorted

    # Fallback: fetch from DB
    db = SessionLocal()
    try:
        bakery_breads = crud.get_bakery_breads(db, bakery_id)

        time_per_bread = {str(bread.bread_type_id): bread.cook_time_s for bread in bakery_breads}

        if time_per_bread:
            await r.set(key, json.dumps(time_per_bread))

        return time_per_bread
    finally:
        db.close()


async def add_customer_to_reservation_dict(r, bakery_id: int, customer_id: int, bread_count_data: dict[str, int]):
    """
    Add or update a customer's reservation in Redis.
    """
    time_per_bread = await get_bakery_time_per_bread(r, bakery_id)
    reservations_key = f"bakery:{bakery_id}:reservations"
    order_key = f"bakery:{bakery_id}:reservation_order"

    reservation = [bread_count_data.get(bid, 0) for bid in time_per_bread.keys()]
    encoded = ",".join(map(str, reservation))

    pipe = r.pipeline()
    pipe.hset(reservations_key, str(customer_id), encoded)
    pipe.zadd(order_key, {str(customer_id): customer_id})
    await pipe.execute()


async def remove_customer_from_reservation_dict(r, bakery_id: int, customer_id: int):
    """
    Remove a single customer's reservation from Redis.
    """
    reservations_key = f"bakery:{bakery_id}:reservations"
    order_key = f"bakery:{bakery_id}:reservation_order"

    pipe = r.pipeline()
    pipe.hdel(reservations_key, str(customer_id))
    pipe.zrem(order_key, str(customer_id))
    await pipe.execute()


async def reset_time_per_bread(r, bakery_id: int):
    """
    Refresh time_per_bread for a bakery from DB into Redis (JSON format).
    """
    db = SessionLocal()
    try:
        bakery_breads = crud.get_bakery_breads(db, bakery_id)
        time_per_bread = {str(bread.bread_type_id): bread.cook_time_s for bread in bakery_breads}

        key = f"bakery:{bakery_id}:time_per_bread"
        if time_per_bread:
            await r.set(key, json.dumps(time_per_bread))

        return time_per_bread
    finally:
        db.close()