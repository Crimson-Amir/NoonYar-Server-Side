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

def get_bakery_reservations(r, bakery_id: int):
    """
    Fetch reservations for a bakery.
    Structure: {customer_id: [bread_counts]}
    """
    key = f"bakery:{bakery_id}:reservations"
    reservations = r.hgetall(key)

    if reservations:
        return {int(k): json.loads(v) for k, v in reservations.items()}

    db = SessionLocal()
    try:
        today_customers = crud.get_today_customers(db, bakery_id)
        time_per_bread = get_bakery_time_per_bread(r, bakery_id)  # reuse other function

        reservation_dict = {}
        for customer in today_customers:
            bread_counts = {bread.bread_type_id: bread.count for bread in customer.bread_associations}
            reservation = [bread_counts.get(bread_id, 0) for bread_id in sorted(time_per_bread.keys())]
            reservation_dict[customer.id] = reservation

        if reservation_dict:
            r.hset(key, mapping={str(k): json.dumps(v) for k, v in reservation_dict.items()})

        return reservation_dict
    finally:
        db.close()


def get_bakery_time_per_bread(r, bakery_id: int):
    """
    Fetch bread type -> cook time mapping for a bakery.
    Structure: {bread_type_id: cook_time_s}
    """
    key = f"bakery:{bakery_id}:time_per_bread"
    time_per_bread = r.hgetall(key)

    if time_per_bread:
        return {
            int(k): int(v)
            for k, v in sorted(time_per_bread.items(), key=lambda item: int(item[0]))
        }
        # return {int(k): int(v) for k, v in time_per_bread.items()}

    # Fallback: fetch from DB
    db = SessionLocal()
    try:
        bakery_breads = crud.get_bakery_breads(db, bakery_id)
        time_per_bread = {bread.bread_type_id: bread.cook_time_s for bread in bakery_breads}

        if time_per_bread:
            r.hset(key, mapping={str(k): v for k, v in time_per_bread.items()})

        return time_per_bread
    finally:
        db.close()

def add_customer_to_reservation_dict(r, bakery_id: int, customer_id: int, bread_count_data: dict[int, int]):
    """
    Add or update a customer's reservation in Redis.
    - bread_count_data: {bread_type_id: count}
    """
    time_per_bread = get_bakery_time_per_bread(r, bakery_id)  # ensure bread types exist
    reservations_key = f"bakery:{bakery_id}:reservations"

    # Ensure reservation list matches the order of time_per_bread keys
    reservation = [bread_count_data.get(bread_id, 0) for bread_id in sorted(time_per_bread.keys())]

    # Save to Redis
    r.hset(reservations_key, str(customer_id), json.dumps(reservation))

def remove_customer_from_reservation_dict(r, bakery_id: int, customer_id: int):
    """
    Remove a single customer's reservation from Redis.
    """
    reservations_key = f"bakery:{bakery_id}:reservations"
    r.hdel(reservations_key, str(customer_id))

def reset_time_per_bread(r, bakery_id: int):
    """
    Reset (refresh) time_per_bread for a bakery from the database into Redis.
    """
    db = SessionLocal()
    try:
        bakery_breads = crud.get_bakery_breads(db, bakery_id)
        time_per_bread = {bread.bread_type_id: bread.cook_time_s for bread in bakery_breads}

        # Save into Redis hash
        key = f"bakery:{bakery_id}:time_per_bread"
        if time_per_bread:
            # overwrite existing values
            r.delete(key)
            r.hset(key, mapping={str(k): v for k, v in time_per_bread.items()})

        return time_per_bread
    finally:
        db.close()
