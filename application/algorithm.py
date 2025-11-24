from typing import List, Dict
from application.helpers import redis_helper
from application.bakery_queue_model import BakeryQueueState


class Algorithm:

    @staticmethod
    async def new_reservation(reservation_dict: Dict[int, List[int]], bread_counts: List[int], r, bakery_id):
        """
        reservation_dict: {position: [bread_counts]}
        bread_counts: list of bread counts (any length)
        """
        total = sum(bread_counts)

        # Load full BakeryQueueState from Redis
        queue_state: BakeryQueueState = await redis_helper.load_queue_state(r, bakery_id)

        # Enforce bread-based current_served: use the higher of
        # - current_served already in queue_state (from previous calls)
        # - current_served derived from breads/prep_state in Redis
        _, _, _, _, _, redis_current_served = await redis_helper.get_slots_state(r, bakery_id)
        if redis_current_served > queue_state.current_served:
            queue_state.current_served = redis_current_served

        # Issue ticket using exact BakeryQueue logic
        if total == 1:
            ticket = queue_state.issue_single()
        else:
            ticket = queue_state.issue_multi(quantity=total)

        # Persist updated state back to Redis
        await redis_helper.save_queue_state(r, bakery_id, queue_state)

        return ticket.number

    @staticmethod
    def compute_bread_time(time_per_bread_list, reserve):
        return sum(
            bread * time_per_bread_list[index]
            for index, bread in enumerate(reserve)
        )

    async def calculate_in_queue_customers_time(self, keys, index, reservation_dict, time_per_bread_list, r=None, bakery_id=None):
        base = sum(
            self.compute_bread_time(time_per_bread_list, reservation_dict[key])
            for key in keys
            if key <= index
        )

        # Apply global timeout (minutes) if provided via Redis
        if r is not None and bakery_id is not None:
            timeout_second = await redis_helper.get_timeout_second(r, bakery_id)
            base += timeout_second
        return base

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
