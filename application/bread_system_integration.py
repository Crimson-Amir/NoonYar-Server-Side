"""
Integration layer between the new bread system and existing FastAPI endpoints.
This bridges the new BakeryQueueSystem with the existing Redis-based system.
"""

from datetime import datetime
from typing import Dict, List, Optional, Any
import json
import asyncio
from redis import asyncio as aioredis

from application.new_bread_system import BakeryQueueSystem, BreadState, Ticket, BreadItem, BREAD_NAMES
from application.helpers import redis_helper
from application.helpers.general_helpers import seconds_until_midnight_iran
from application.new_bread_system_tasks import (
    load_bread_system,
    save_bread_system,
    start_bread_baking_timer,
)
from application import crud
from application.database import SessionLocal
from application.setting import settings


class BreadSystemIntegration:
    """
    Integration class that syncs the new bread system with existing Redis data.
    This allows gradual migration while maintaining compatibility.
    """

    def __init__(self, bakery_id: int):
        self.bakery_id = bakery_id

    async def get_or_create_system(self, r) -> BakeryQueueSystem:
        """Get or create the bread system for this bakery"""
        system = await load_bread_system(r, self.bakery_id)
        return system

    async def sync_from_redis(self, r) -> BakeryQueueSystem:
        """
        Sync the new bread system state from existing Redis data.
        This is useful for initial migration or recovery.
        """
        system = await self.get_or_create_system(r)

        # Get existing reservations from Redis
        time_per_bread = await redis_helper.get_bakery_time_per_bread(r, self.bakery_id)
        reservations = await redis_helper.get_bakery_reservations(r, self.bakery_id)
        wait_list = await redis_helper.get_bakery_wait_list(r, self.bakery_id)

        # Get urgent items
        urgent_items = await redis_helper.list_urgent_items(r, self.bakery_id)

        # Sync tickets - only add ones that don't exist in our system
        existing_numbers = {t.number for t in system.all_tickets_history}

        bread_ids_sorted = sorted(time_per_bread.keys())

        # Add normal tickets from reservations
        for ticket_id, counts in reservations.items():
            if int(ticket_id) in existing_numbers:
                continue

            # Convert counts list to match bread type indices
            counts_list = self._convert_counts_to_list(counts, bread_ids_sorted)

            ticket = Ticket(
                number=int(ticket_id),
                counts=counts_list,
                is_urgent=False,
                created_at=datetime.now(),
            )
            system.all_tickets_history.append(ticket)

            # Check if it's in wait list
            if ticket_id in wait_list:
                # Ticket has been served, mark as done
                for bread in ticket.breads:
                    bread.state = BreadState.DELIVERED.value
                ticket.is_delivered = True
            else:
                system.normal_queue.append(ticket)

        # Add urgent tickets
        for item in urgent_items.get("items", []):
            ticket_id = item.get("ticket_id")
            if ticket_id and int(ticket_id) not in existing_numbers:
                original_breads = item.get("original_breads", "")
                counts_list = [int(x) for x in original_breads.split(",") if x]

                urgent_ticket = Ticket(
                    number=int(ticket_id),
                    counts=counts_list,
                    is_urgent=True,
                    created_at=datetime.now(),
                )
                system.all_tickets_history.append(urgent_ticket)
                system.urgent_queue.append(urgent_ticket)

        # Sort all lists
        system.all_tickets_history.sort(key=lambda x: x.number)
        system.normal_queue.sort(key=lambda x: x.number)
        system.urgent_queue.sort(key=lambda x: x.number)

        # Update consumed numbers
        for ticket in system.all_tickets_history:
            system.consumed_numbers.add(ticket.number)

        # Determine parity if not already set
        if not system.parity_determined and system.all_tickets_history:
            first_ticket = system.all_tickets_history[0]
            system._determine_parity(first_ticket.type_name)

        await save_bread_system(r, self.bakery_id, system)
        return system

    def _convert_counts_to_list(self, counts: List[int], bread_ids_sorted: List[str]) -> List[int]:
        """Convert counts from Redis format to list indexed by bread type"""
        # Map bread IDs to indices 0-3
        result = [0, 0, 0, 0]
        for i, bid in enumerate(bread_ids_sorted):
            idx = int(bid) - 1  # Assuming bread IDs are 1, 2, 3, 4
            if 0 <= idx < 4 and i < len(counts):
                result[idx] = counts[i]
        return result

    async def create_ticket(
        self,
        r,
        bread_requirements: Dict[str, int],
        time_per_bread: Dict[str, int]
    ) -> tuple[Optional[int], str]:
        """
        Create a new ticket using the new bread system.
        Returns (ticket_number, message).
        """
        system = await self.get_or_create_system(r)

        # Convert bread requirements to counts list
        bread_ids_sorted = sorted(time_per_bread.keys())
        counts_list = [int(bread_requirements.get(str(bid), 0)) for bid in bread_ids_sorted]

        # Pad to 4 types if needed
        while len(counts_list) < 4:
            counts_list.append(0)
        counts_list = counts_list[:4]  # Only keep first 4

        # Request ticket
        ticket, message = system.request_ticket(counts_list)

        if ticket:
            await save_bread_system(r, self.bakery_id, system)
            return ticket.number, message

        return None, message

    async def inject_urgent_bread(
        self,
        r,
        ticket_number: int,
        bread_requirements: Dict[str, int],
        time_per_bread: Dict[str, int]
    ) -> tuple[bool, str]:
        """
        Inject urgent bread for an existing ticket.
        """
        system = await self.get_or_create_system(r)

        # Convert to counts list
        bread_ids_sorted = sorted(time_per_bread.keys())
        counts_list = [int(bread_requirements.get(str(bid), 0)) for bid in bread_ids_sorted]

        while len(counts_list) < 4:
            counts_list.append(0)
        counts_list = counts_list[:4]

        success, message = system.request_urgent_bread(ticket_number, counts_list)

        if success:
            await save_bread_system(r, self.bakery_id, system)

        return success, message

    async def edit_ticket(
        self,
        r,
        ticket_number: int,
        new_requirements: Dict[str, int],
        time_per_bread: Dict[str, int]
    ) -> tuple[bool, str]:
        """
        Edit a ticket that is still in queue.
        """
        system = await self.get_or_create_system(r)

        # Convert to counts list
        bread_ids_sorted = sorted(time_per_bread.keys())
        counts_list = [int(new_requirements.get(str(bid), 0)) for bid in bread_ids_sorted]

        while len(counts_list) < 4:
            counts_list.append(0)
        counts_list = counts_list[:4]

        success, message = system.edit_ticket(ticket_number, counts_list)

        if success:
            await save_bread_system(r, self.bakery_id, system)

        return success, message

    async def cancel_ticket(
        self,
        r,
        ticket_number: int
    ) -> tuple[bool, str]:
        """
        Cancel a ticket and burn its slot.
        """
        system = await self.get_or_create_system(r)

        success, message = system.cancel_ticket(ticket_number)

        if success:
            await save_bread_system(r, self.bakery_id, system)

        return success, message

    async def put_bread_in_oven(
        self,
        r,
        ticket_number: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Put the next waiting bread into the oven.
        """
        system = await self.get_or_create_system(r)

        # If specific ticket requested
        if ticket_number:
            # Find the ticket and set as current task
            for ticket in system.all_tickets_history:
                if ticket.number == ticket_number and not ticket.is_cancelled:
                    system.current_baker_task = ticket
                    if ticket.is_urgent:
                        # Remove from urgent queue if present
                        if ticket in system.urgent_queue:
                            system.urgent_queue.remove(ticket)
                    else:
                        system.active_normal_ticket = ticket
                    break

        # Put bread in oven
        result_msg = system.put_bread_in_oven()

        # Start baking timer for the bread that was put in oven
        if system.current_baker_task:
            ticket = system.current_baker_task
            bread = ticket.get_next_waiting_bread()
            if bread:
                bread_idx = ticket.breads.index(bread)
                bread.start_baking()

                # Start Celery timer
                baking_time = await redis_helper.get_baking_time_s(r, self.bakery_id)
                start_bread_baking_timer.apply_async(
                    args=[self.bakery_id, ticket.number, bread_idx],
                    countdown=0  # Start immediately, completion is scheduled
                )

        await save_bread_system(r, self.bakery_id, system)

        return {
            "status": "success",
            "message": result_msg,
            "current_ticket": system.current_baker_task.number if system.current_baker_task else None,
        }

    async def deliver_ticket(
        self,
        r,
        ticket_number: int
    ) -> Dict[str, Any]:
        """
        Deliver a ticket.
        """
        system = await self.get_or_create_system(r)

        result = system.deliver_ticket(ticket_number)

        await save_bread_system(r, self.bakery_id, system)

        return {
            "status": "success",
            "message": result,
        }

    async def get_baker_status(self, r) -> Dict[str, Any]:
        """
        Get current baker status.
        """
        system = await self.get_or_create_system(r)
        return system.get_baker_status_display()

    async def get_ticket_status(self, r, ticket_number: int) -> Dict[str, Any]:
        """
        Get detailed ticket status.
        """
        system = await self.get_or_create_system(r)
        return system.get_ticket_status(ticket_number)

    async def get_dashboard(self, r) -> Dict[str, Any]:
        """
        Get full dashboard status.
        """
        system = await self.get_or_create_system(r)
        return system.get_full_dashboard()

    async def check_baking_progress(self, r) -> List[int]:
        """
        Check all baking breads and update their state.
        Returns list of newly ready tickets.
        """
        system = await self.get_or_create_system(r)
        baking_time = await redis_helper.get_baking_time_s(r, self.bakery_id)
        system.baking_time_seconds = baking_time

        newly_ready = system.check_and_update_bread_states()

        if newly_ready:
            await save_bread_system(r, self.bakery_id, system)

        return newly_ready


# Convenience functions for use in endpoints


async def get_bread_system_integration(bakery_id: int) -> BreadSystemIntegration:
    """Get a bread system integration instance"""
    return BreadSystemIntegration(bakery_id)


async def new_ticket_with_new_system(
    r,
    bakery_id: int,
    bread_requirements: Dict[str, int],
    time_per_bread: Dict[str, int]
) -> tuple[Optional[int], str]:
    """
    Create a new ticket using the new bread system.
    This can be called from the new_ticket endpoint.
    """
    integration = await get_bread_system_integration(bakery_id)
    return await integration.create_ticket(r, bread_requirements, time_per_bread)


async def inject_urgent_with_new_system(
    r,
    bakery_id: int,
    ticket_number: int,
    bread_requirements: Dict[str, int],
    time_per_bread: Dict[str, int]
) -> tuple[bool, str]:
    """
    Inject urgent bread using the new system.
    """
    integration = await get_bread_system_integration(bakery_id)
    return await integration.inject_urgent_bread(r, ticket_number, bread_requirements, time_per_bread)


async def edit_ticket_with_new_system(
    r,
    bakery_id: int,
    ticket_number: int,
    new_requirements: Dict[str, int],
    time_per_bread: Dict[str, int]
) -> tuple[bool, str]:
    """
    Edit ticket using the new system.
    """
    integration = await get_bread_system_integration(bakery_id)
    return await integration.edit_ticket(r, ticket_number, new_requirements, time_per_bread)


async def cancel_ticket_with_new_system(
    r,
    bakery_id: int,
    ticket_number: int
) -> tuple[bool, str]:
    """
    Cancel ticket using the new system.
    """
    integration = await get_bread_system_integration(bakery_id)
    return await integration.cancel_ticket(r, ticket_number)


async def put_bread_in_oven_with_new_system(
    r,
    bakery_id: int,
    ticket_number: Optional[int] = None
) -> Dict[str, Any]:
    """
    Put bread in oven using the new system.
    """
    integration = await get_bread_system_integration(bakery_id)
    return await integration.put_bread_in_oven(r, ticket_number)


async def deliver_ticket_with_new_system(
    r,
    bakery_id: int,
    ticket_number: int
) -> Dict[str, Any]:
    """
    Deliver ticket using the new system.
    """
    integration = await get_bread_system_integration(bakery_id)
    return await integration.deliver_ticket(r, ticket_number)


async def get_baker_status_with_new_system(
    r,
    bakery_id: int
) -> Dict[str, Any]:
    """
    Get baker status using the new system.
    """
    integration = await get_bread_system_integration(bakery_id)
    return await integration.get_baker_status(r)


async def get_dashboard_with_new_system(
    r,
    bakery_id: int
) -> Dict[str, Any]:
    """
    Get dashboard using the new system.
    """
    integration = await get_bread_system_integration(bakery_id)
    return await integration.get_dashboard(r)
