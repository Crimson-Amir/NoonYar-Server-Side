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
            reservation_dict[1] = bread_counts
            return

        last_key = keys[-1]
        last_sum = sum(reservation_dict[last_key])

        if total == 1:
            for i in range(len(keys) - 1):
                if sum(reservation_dict[keys[i]]) > 1 and sum(reservation_dict[keys[i + 1]]) > 1:
                    reservation_dict[keys[i] + 1] = bread_counts
                    return

            new_key = 1 if not keys else keys[-1] + (2 if last_sum == 1 else 1)
            reservation_dict[new_key] = bread_counts

        else:
            last_multiple = 0
            for key in reversed(keys):
                if sum(reservation_dict[key]) > 1:
                    last_multiple = key
                    break
            distance = (keys[-1] - last_multiple) // 2
            if last_multiple == last_key:
                reservation_dict[last_key + 2] = bread_counts

            elif distance < total and last_sum == 1:
                reservation_dict[last_key + 1] = bread_counts

            else:
                reservation_dict[last_multiple + (total * 2)] = bread_counts

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
    key = f"bakery:{bakery_id}:reservations"
    reservations = await r.hgetall(key)

    if reservations:
        return {int(k): json.loads(v) for k, v in reservations.items()}

    db = SessionLocal()
    try:
        today_customers = crud.get_today_customers(db, bakery_id)
        time_per_bread = await get_bakery_time_per_bread(r, bakery_id)  # reuse other function

        reservation_dict = {}
        for customer in today_customers:
            bread_counts = {bread.bread_type_id: bread.count for bread in customer.bread_associations}
            reservation = [bread_counts.get(int(bread_id), 0) for bread_id in time_per_bread.keys()]
            reservation_dict[customer.id] = reservation

        if reservation_dict:
            await r.hset(key, mapping={str(k): json.dumps(v) for k, v in reservation_dict.items()})

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


async def add_customer_to_reservation_dict(r, bakery_id: int, customer_id: int, bread_count_data: dict[int, int]):
    """
    Add or update a customer's reservation in Redis.
    - bread_count_data: {bread_type_id: count}
    """
    time_per_bread = await get_bakery_time_per_bread(r, bakery_id)
    reservations_key = f"bakery:{bakery_id}:reservations"

    # Ensure reservation list matches the order of time_per_bread keys
    reservation = [bread_count_data.get(int(bread_id), 0) for bread_id in time_per_bread.keys()]

    # Save to Redis
    await r.hset(reservations_key, str(customer_id), json.dumps(reservation))

async def remove_customer_from_reservation_dict(r, bakery_id: int, customer_id: int):
    """
    Remove a single customer's reservation from Redis.
    """
    reservations_key = f"bakery:{bakery_id}:reservations"
    await r.hdel(reservations_key, str(customer_id))


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