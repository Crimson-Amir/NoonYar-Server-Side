import crud
from database import SessionLocal

class Algorithm:

    @staticmethod
    def new_reservation(reservation_dict, bread1_c, bread2_c, bread3_c):
        total = bread1_c + bread2_c + bread3_c
        keys = sorted(reservation_dict.keys())

        if not keys:
            reservation_dict[1] = [bread1_c, bread2_c, bread3_c]
            return

        last_key = keys[-1]
        last_sum = sum(reservation_dict[last_key])

        if total == 1:
            for i in range(len(keys) - 1):
                if sum(reservation_dict[keys[i]]) > 1 and sum(reservation_dict[keys[i + 1]]) > 1:
                    reservation_dict[keys[i] + 1] = [bread1_c, bread2_c, bread3_c]
                    return

            new_key = 1 if not keys else keys[-1] + (2 if last_sum == 1 else 1)
            reservation_dict[new_key] = [bread1_c, bread2_c, bread3_c]

        else:
            last_multiple = 0
            for key in reversed(keys):
                if sum(reservation_dict[key]) > 1:
                    last_multiple = key
                    break
            distance = (keys[-1] - last_multiple) // 2
            if last_multiple == last_key:
                reservation_dict[last_key + 2] = [bread1_c, bread2_c, bread3_c]

            elif distance < total and last_sum == 1:
                reservation_dict[last_key + 1] = [bread1_c, bread2_c, bread3_c]

            else:
                reservation_dict[last_multiple + (total * 2)] = [bread1_c, bread2_c, bread3_c]

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
        time, consecutive_empty, consecutive_full = 0, 0, 0

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

bakery_data = {}

def get_bakery_data(bakery_id):
    if bakery_id not in bakery_data:
        db = SessionLocal()

        try:
            bakery_breads = crud.get_bakery_breads(db, bakery_id)
            today_customers = crud.get_today_customers(db, bakery_id)

            time_per_bread = {bread.bread_type_id: bread.cook_time_s for bread in bakery_breads}

            reservation_dict = {}
            for customer in today_customers:
                bread_counts = {bread.bread_type_id: bread.count for bread in customer.bread_associations}
                reservation = [bread_counts.get(bread_id, 0) for bread_id in time_per_bread]
                reservation_dict[customer.hardware_customer_id] = reservation

            bakery_data[bakery_id] = {
                'reservation_dict': reservation_dict,
                'time_per_bread': time_per_bread
            }
        finally:
            db.close()

    return bakery_data[bakery_id]


def remove_customer_from_reservation_dict(bakery_id, hardware_customer_id):
    data = get_bakery_data(bakery_id)
    for i in range(hardware_customer_id + 1):
        data['reservation_dict'].pop(i, None)
    return data


def add_customer_to_reservation_dict(bakery_id, hardware_customer_id, bread_count_data):
    data = get_bakery_data(bakery_id)
    reservation = [bread_count_data.get(str(bread_id), 0) for bread_id in data['time_per_bread']]
    data['reservation_dict'][hardware_customer_id] = reservation
    return data


def reset_time_per_bread(bakery_id):
    db = SessionLocal()
    try:
        bakery_breads = crud.get_bakery_breads(db, bakery_id)
        time_per_bread = {bread.bread_type_id: bread.cook_time_s for bread in bakery_breads}
        bakery_data[bakery_id]['time_per_bread'] = time_per_bread
    finally:
        db.close()