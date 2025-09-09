import private
from typing import List, Dict
from helpers import redis_helper

class Algorithm:

    @staticmethod
    async def new_reservation(reservation_dict: Dict[int, List[int]], bread_counts: List[int], r, bakery_id):
        """
        reservation_dict: {position: [bread_counts]}
        bread_counts: list of bread counts (any length)
        """
        total = sum(bread_counts)
        keys = sorted(reservation_dict.keys())

        if not keys:
            last_key = await redis_helper.get_last_ticket_number(r, bakery_id) or 0
            return last_key + 1

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
