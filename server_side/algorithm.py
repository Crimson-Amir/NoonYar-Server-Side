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

    def exist_customer_time(self, time_per_bread, keys, reservation_dict):
        return sum(self.compute_bread_time(time_per_bread, reservation_dict[reserve]) for reserve in keys)

    @staticmethod
    def compute_empty_slot_time(keys, reservation_dict):
        time, consecutive_empty, consecutive_full = 0, 0, 0

        prev_sum = sum(reservation_dict[keys[0]]) if keys else 0  # Handle empty keys case

        for i in range(1, len(keys)):
            curr_sum = sum(reservation_dict[keys[i]])

            if prev_sum == 1 and curr_sum == 1:
                consecutive_empty += 1
            else:
                consecutive_empty = 0

            if prev_sum > 1 and curr_sum > 1:
                consecutive_full += 1

            prev_sum = curr_sum

        return consecutive_empty + consecutive_full


bakery_data = {}

def get_dicts(bakery_id):
    if bakery_data not in bakery_data:
        db = SessionLocal()

        reservation_dict = {}
        time_per_bread = {}

        bakery_breads = crud.get_bakery_breads(db, bakery_id)

        for bread in bakery_breads:
            time_per_bread[bread.bread_type_id] = bread.cook_time_s

        today_customers = crud.get_today_customers(db, bakery_id)

        for customer in today_customers:
            dict1 = {}
            for bread in customer.bread_associations:
                dict1[bread.bread_id] = bread.count

            reservation_dict[customer.hardware_customer_id] = [list({key: dict1.get(key, 0) for key in time_per_bread}.values())]

        bakery_data[bakery_id] = {
            'reservation_dict': reservation_dict,
            'time_per_bread': time_per_bread
        }