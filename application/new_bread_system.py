"""
New Bread System Logic - Enterprise Bakery Queue System

This module implements the new bread system algorithm with:
- Bread states: WAITING, BAKING, READY, DELIVERED
- Ticket types: single (1 bread) vs multi (multiple breads)
- Parity-based slot assignment system
- Urgent bread injection
- Edit/Cancel functionality (only for tickets in queue)
- Celery-based baking timers instead of threading
"""

from enum import Enum
from datetime import datetime
from typing import Dict, List, Optional, Set, Any
from dataclasses import dataclass, field
import time
import json


# --- Constants & Configuration ---


class BreadType(Enum):
    """Bread type indices matching the original system"""
    SADE = 0
    KONJED = 1
    BOZORG_SADE = 2
    BOZORG_KONJED = 3


# Map bread type IDs to names (will be populated from database)
BREAD_NAMES = {0: "Sade", 1: "Konjed", 2: "Bozorg Sade", 3: "Bozorg Konjed"}


class BreadState(Enum):
    """State of individual bread items"""
    WAITING = "Waiting"
    BAKING = "Baking"
    READY = "Ready"
    DELIVERED = "Delivered"


class TicketState(Enum):
    """Overall ticket state"""
    IN_QUEUE = "In Queue"
    PREPARING = "Preparing"
    BAKING = "Baking"
    READY = "Ready"
    DELIVERED = "Delivered"
    CANCELLED = "Cancelled"


class Config:
    """Configuration for baking times"""
    BAKING_TIME_SECONDS = 600  # Default 10 minutes, configurable per bakery


# --- Domain Models ---


@dataclass
class BreadItem:
    """Individual bread item with state tracking"""
    type_idx: int
    state: str = field(default="Waiting")
    baking_start_time: Optional[float] = field(default=None)
    ready_time: Optional[float] = field(default=None)

    def __post_init__(self):
        if self.state == "Baking" and self.baking_start_time is None:
            self.baking_start_time = time.time()

    @property
    def type_name(self) -> str:
        return BREAD_NAMES.get(self.type_idx, f"Type_{self.type_idx}")

    def start_baking(self) -> None:
        """Mark bread as started baking"""
        self.state = BreadState.BAKING.value
        self.baking_start_time = time.time()

    def check_if_ready(self, baking_time_seconds: int) -> bool:
        """Check if bread is ready based on baking time"""
        if self.state == BreadState.BAKING.value and self.baking_start_time:
            elapsed = time.time() - self.baking_start_time
            if elapsed >= baking_time_seconds:
                self.state = BreadState.READY.value
                self.ready_time = time.time()
                return True
        return False

    def mark_delivered(self) -> bool:
        """Mark bread as delivered"""
        if self.state == BreadState.READY.value:
            self.state = BreadState.DELIVERED.value
            return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type_idx": self.type_idx,
            "type_name": self.type_name,
            "state": self.state,
            "baking_start_time": self.baking_start_time,
            "ready_time": self.ready_time,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BreadItem":
        item = cls(
            type_idx=data["type_idx"],
            state=data.get("state", "Waiting"),
            baking_start_time=data.get("baking_start_time"),
            ready_time=data.get("ready_time"),
        )
        return item

    def __repr__(self) -> str:
        if self.state == BreadState.BAKING.value and self.baking_start_time:
            elapsed = int(time.time() - self.baking_start_time)
            remaining = max(0, Config.BAKING_TIME_SECONDS - elapsed)
            return f"[{self.type_name}: {remaining}s]"
        return f"[{self.type_name}: {self.state}]"


@dataclass
class Ticket:
    """Ticket representing a customer's order"""
    number: int
    counts: List[int]  # Count of each bread type
    is_urgent: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    is_delivered: bool = False
    is_cancelled: bool = False
    breads: List[BreadItem] = field(default_factory=list)

    def __post_init__(self):
        if not self.breads:
            self.breads = []
            for type_idx, count in enumerate(self.counts):
                for _ in range(count):
                    self.breads.append(BreadItem(type_idx))

    @property
    def total_count(self) -> int:
        return sum(self.counts)

    @property
    def type_name(self) -> str:
        return "single" if self.total_count == 1 else "multi"

    @property
    def waiting_count(self) -> int:
        return sum(1 for b in self.breads if b.state == BreadState.WAITING.value)

    @property
    def baking_count(self) -> int:
        return sum(1 for b in self.breads if b.state == BreadState.BAKING.value)

    @property
    def ready_count(self) -> int:
        return sum(1 for b in self.breads if b.state == BreadState.READY.value)

    @property
    def delivered_count(self) -> int:
        return sum(1 for b in self.breads if b.state == BreadState.DELIVERED.value)

    def is_fully_baked(self) -> bool:
        """Check if all breads are ready or delivered"""
        if self.is_cancelled:
            return False
        return all(
            b.state in [BreadState.READY.value, BreadState.DELIVERED.value]
            for b in self.breads
        )

    def is_fully_processed_by_baker(self) -> bool:
        """Check if all breads have been put in oven"""
        return self.waiting_count == 0

    def get_next_waiting_bread(self) -> Optional[BreadItem]:
        """Get the next bread waiting to be baked"""
        for b in self.breads:
            if b.state == BreadState.WAITING.value:
                return b
        return None

    def deliver(self) -> tuple[bool, str]:
        """Deliver all ready breads in this ticket"""
        if self.is_cancelled:
            return False, "Ticket is Cancelled."
        if self.is_delivered:
            return False, "Already delivered."
        if not self.is_fully_baked():
            return False, "Not fully baked yet."

        for b in self.breads:
            b.mark_delivered()
        self.is_delivered = True
        return True, "Delivered."

    def get_summary_str(self) -> str:
        """Get a summary of the ticket contents"""
        if self.is_cancelled:
            return "CANCELLED"
        parts = []
        for idx, count in enumerate(self.counts):
            if count > 0:
                parts.append(f"{count} {BREAD_NAMES.get(idx, f'Type_{idx}')}")
        return ", ".join(parts) if parts else "Empty"

    def get_state(self) -> str:
        """Get the overall ticket state"""
        if self.is_cancelled:
            return TicketState.CANCELLED.value
        if self.is_delivered:
            return TicketState.DELIVERED.value
        if self.is_fully_baked():
            return TicketState.READY.value
        if self.baking_count > 0:
            return TicketState.BAKING.value
        return TicketState.IN_QUEUE.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "number": self.number,
            "counts": self.counts,
            "is_urgent": self.is_urgent,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "is_delivered": self.is_delivered,
            "is_cancelled": self.is_cancelled,
            "breads": [b.to_dict() for b in self.breads],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Ticket":
        ticket = cls(
            number=data["number"],
            counts=data["counts"],
            is_urgent=data.get("is_urgent", False),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            is_delivered=data.get("is_delivered", False),
            is_cancelled=data.get("is_cancelled", False),
        )
        if "breads" in data:
            ticket.breads = [BreadItem.from_dict(b) for b in data["breads"]]
        return ticket


# --- Core System Logic ---


class BakeryQueueSystem:
    """
    Main queue system for bakery ticket management.
    Implements parity-based slot assignment for singles and multis.
    """

    def __init__(self, baking_time_seconds: int = 600):
        self.baking_time_seconds = baking_time_seconds

        self.all_tickets_history: List[Ticket] = []
        self.normal_queue: List[Ticket] = []
        self.urgent_queue: List[Ticket] = []

        self.consumed_numbers: Set[int] = set()
        self.parity_determined: bool = False
        self.single_parity: Optional[int] = None
        self.multi_parity: Optional[int] = None

        self.active_normal_ticket: Optional[Ticket] = None
        self.current_baker_task: Optional[Ticket] = None

    def _determine_parity(self, first_type: str) -> None:
        """Determine slot parity based on first ticket type"""
        if self.parity_determined:
            return
        if first_type == "single":
            self.single_parity = 1  # Odd numbers
            self.multi_parity = 0   # Even numbers
        else:
            self.multi_parity = 1   # Odd numbers
            self.single_parity = 0  # Even numbers
        self.parity_determined = True

    def _get_global_max(self) -> int:
        """Get the maximum consumed number"""
        return max(self.consumed_numbers) if self.consumed_numbers else 0

    def _get_threshold_number(self) -> int:
        """Get the threshold number for slot calculation"""
        threshold = 0
        if self.active_normal_ticket:
            threshold = max(threshold, self.active_normal_ticket.number)
        if self.current_baker_task and not self.current_baker_task.is_urgent:
            threshold = max(threshold, self.current_baker_task.number)
        return threshold

    def _find_valid_slots(self, target_parity: int) -> List[int]:
        """Find valid available slots with given parity"""
        threshold = self._get_threshold_number()
        global_max = self._get_global_max()
        valid_slots = []
        for num in range(threshold + 1, global_max):
            if num not in self.consumed_numbers:
                if num % 2 == target_parity:
                    valid_slots.append(num)
        return valid_slots

    def _get_next_sequence_number(self, target_parity: int) -> int:
        """Get the next available sequence number with correct parity"""
        start_point = max(self._get_global_max(), self._get_threshold_number())
        search = start_point + 1
        while search % 2 != target_parity:
            search += 1
        return search

    def _assign_next_task_to_baker(self) -> None:
        """Assign the next task to the baker"""
        if self.current_baker_task is not None:
            return

        # Urgent queue has priority
        if self.urgent_queue:
            self.current_baker_task = self.urgent_queue.pop(0)
            return

        # Then normal queue
        if self.normal_queue:
            next_normal = self.normal_queue.pop(0)
            self.current_baker_task = next_normal
            self.active_normal_ticket = next_normal
            return

        self.current_baker_task = None

    def request_ticket(self, counts_list: List[int]) -> tuple[Optional[Ticket], str]:
        """
        Request a new ticket with given bread counts.
        Returns (ticket, message).
        """
        total = sum(counts_list)
        if total == 0:
            return None, "Count cannot be zero."

        type_name = "single" if total == 1 else "multi"

        if not self.parity_determined:
            self._determine_parity(type_name)

        target_parity = self.single_parity if type_name == "single" else self.multi_parity

        assigned_number = None
        slots_to_consume = []
        valid_slots = self._find_valid_slots(target_parity)

        if type_name == "single":
            if valid_slots:
                assigned_number = valid_slots[0]
                slots_to_consume = [assigned_number]
            else:
                assigned_number = self._get_next_sequence_number(target_parity)
                slots_to_consume = [assigned_number]

        elif type_name == "multi":
            if len(valid_slots) >= total:
                slots_to_consume = valid_slots[:total]
                assigned_number = slots_to_consume[-1]
            else:
                assigned_number = self._get_next_sequence_number(target_parity)
                slots_to_consume = [assigned_number]

        new_ticket = Ticket(assigned_number, counts_list, is_urgent=False)

        self.all_tickets_history.append(new_ticket)
        self.all_tickets_history.sort(key=lambda x: x.number)

        self.normal_queue.append(new_ticket)
        self.normal_queue.sort(key=lambda x: x.number)

        for num in slots_to_consume:
            self.consumed_numbers.add(num)

        if self.current_baker_task is None:
            self._assign_next_task_to_baker()

        return new_ticket, "Success"

    def request_urgent_bread(self, ticket_number: int, counts_list: List[int]) -> tuple[bool, str]:
        """
        Request urgent bread for an existing ticket.
        Returns (success, message).
        """
        original_ticket = next(
            (
                t
                for t in self.all_tickets_history
                if t.number == ticket_number
                and not t.is_urgent
                and not t.is_cancelled
            ),
            None,
        )

        if not original_ticket:
            return False, "Valid Original Ticket number not found."

        is_in_queue = original_ticket in self.normal_queue
        if is_in_queue:
            return (
                False,
                "Ticket is still in queue. Wait for processing or Edit it.",
            )

        urgent_ticket = Ticket(ticket_number, counts_list, is_urgent=True)
        self.urgent_queue.append(urgent_ticket)
        self.urgent_queue.sort(key=lambda x: x.number)
        self.all_tickets_history.append(urgent_ticket)

        if self.current_baker_task is None:
            self._assign_next_task_to_baker()

        return True, f"Urgent order for Ticket #{ticket_number} added."

    def edit_ticket(self, ticket_number: int, new_counts: List[int]) -> tuple[bool, str]:
        """
        Edit a ticket that is still in the normal queue.
        Returns (success, message).
        """
        target_ticket = next(
            (
                t
                for t in self.all_tickets_history
                if t.number == ticket_number
                and not t.is_urgent
                and not t.is_cancelled
            ),
            None,
        )

        if not target_ticket:
            return False, "Ticket not found or already cancelled."

        if target_ticket not in self.normal_queue:
            return (
                False,
                "Cannot edit: Ticket is already being processed or finished.",
            )

        new_total = sum(new_counts)
        if new_total == 0:
            return False, "Count cannot be zero."

        original_is_single = target_ticket.total_count == 1
        if original_is_single and new_total > 1:
            return False, "Error: Single ticket cannot be upgraded to Multi-bread."

        # Update ticket content
        target_ticket.counts = new_counts
        target_ticket.breads = []
        for type_idx, count in enumerate(new_counts):
            for _ in range(count):
                target_ticket.breads.append(BreadItem(type_idx))

        return True, f"Ticket #{ticket_number} updated successfully."

    def cancel_ticket(self, ticket_number: int) -> tuple[bool, str]:
        """
        Cancel a ticket that is still in the normal queue.
        The slot is burned (cannot be reused).
        Returns (success, message).
        """
        target_ticket = next(
            (
                t
                for t in self.all_tickets_history
                if t.number == ticket_number
                and not t.is_urgent
                and not t.is_cancelled
            ),
            None,
        )

        if not target_ticket:
            return False, "Ticket not found or already cancelled."

        if target_ticket not in self.normal_queue:
            return (
                False,
                "Cannot cancel: Ticket is already being processed or finished.",
            )

        self.normal_queue.remove(target_ticket)
        target_ticket.is_cancelled = True

        return True, f"Ticket #{ticket_number} cancelled. Slot burned."

    def put_bread_in_oven(self) -> str:
        """
        Put the next waiting bread into the oven.
        Returns status message.
        """
        if self.current_baker_task is None:
            self._assign_next_task_to_baker()

        if self.current_baker_task is None:
            return "No active orders."

        current_t = self.current_baker_task
        bread = current_t.get_next_waiting_bread()

        if bread:
            bread.start_baking()
            if not current_t.is_urgent:
                self.active_normal_ticket = current_t

            msg = (
                f"Action: 1 {bread.type_name} -> OVEN (Ticket #{current_t.number})"
            )

            if current_t.is_fully_processed_by_baker():
                msg += "\n✅ Ticket batch finished. Baker free."
                self.current_baker_task = None
                self._assign_next_task_to_baker()
                if self.current_baker_task:
                    msg += f"\nNext Locked Task: #{self.current_baker_task.number}"

            return msg
        else:
            self.current_baker_task = None
            self._assign_next_task_to_baker()
            return "Error: Ticket has no waiting bread."

    def deliver_ticket(self, ticket_number: int) -> str:
        """
        Deliver a ticket if all its breads are ready.
        Returns delivery report.
        """
        related_tickets = [
            t
            for t in self.all_tickets_history
            if t.number == ticket_number and not t.is_cancelled
        ]

        if not related_tickets:
            return "Ticket not found or cancelled."

        all_delivered_already = all(t.is_delivered for t in related_tickets)
        if all_delivered_already:
            return f"Ticket #{ticket_number} is ALREADY DELIVERED."

        all_ready = all(t.is_fully_baked() for t in related_tickets)
        if not all_ready:
            return "Cannot deliver. Some breads are still baking or waiting."

        report = f"📦 DELIVERING TICKET #{ticket_number}\n" + "-" * 30 + "\n"

        for t in related_tickets:
            success, msg = t.deliver()
            prefix = "Urgent" if t.is_urgent else "Main"
            status_str = (
                " (Already Delivered)" if msg == "Already delivered." else ""
            )
            report += f"{prefix}: {t.get_summary_str()}{status_str}\n"

        report += "-" * 30 + "\n✅ Handed to customer."
        return report

    def get_baker_status_display(self) -> Dict[str, Any]:
        """Get current baker status for API response"""
        if self.current_baker_task:
            t = self.current_baker_task
            status_type = "URGENT" if t.is_urgent else "Normal"
            processed = t.total_count - t.waiting_count
            return {
                "status": "preparing",
                "ticket_number": t.number,
                "ticket_type": status_type,
                "order_summary": t.get_summary_str(),
                "progress": f"{processed}/{t.total_count}",
                "processed": processed,
                "total": t.total_count,
                "waiting": t.waiting_count,
                "baking": t.baking_count,
                "ready": t.ready_count,
            }
        return {
            "status": "idle",
            "message": "Baker is IDLE (No orders locked)."
        }

    def get_ticket_status(self, ticket_number: int) -> Dict[str, Any]:
        """Get detailed status for a specific ticket"""
        tickets = [t for t in self.all_tickets_history if t.number == ticket_number]
        if not tickets:
            return {"error": "Ticket not found"}

        main_ticket = next((t for t in tickets if not t.is_urgent), None)
        urgent_tickets = [t for t in tickets if t.is_urgent]

        result = {
            "ticket_number": ticket_number,
            "state": self._get_ticket_state_display(tickets),
            "main_ticket": main_ticket.to_dict() if main_ticket else None,
            "urgent_tickets": [t.to_dict() for t in urgent_tickets],
        }
        return result

    def _get_ticket_state_display(self, tickets: List[Ticket]) -> str:
        """Determine overall state for a group of tickets (main + urgent)"""
        if all(t.is_cancelled for t in tickets):
            return "❌ CANCELLED"
        if all(t.is_delivered for t in tickets):
            return "📦 Delivered"
        if all(t.is_fully_baked() for t in tickets):
            return "✅ Ready"
        if any(t == self.current_baker_task for t in tickets):
            return "🔁 Preparing"
        if any(t.baking_count > 0 for t in tickets):
            return "🔥 Baking"
        return "📄 In Queue"

    def get_full_dashboard(self) -> Dict[str, Any]:
        """Get full dashboard status"""
        # Current task
        current_task = None
        if self.current_baker_task:
            t = self.current_baker_task
            st = "URGENT" if t.is_urgent else "Normal"
            current_task = {
                "ticket_number": t.number,
                "type": st,
                "status": "preparing",
            }

        # Waiting queues
        urgent_list = [{"number": t.number, "summary": t.get_summary_str()} for t in self.urgent_queue]
        normal_list = [{"number": t.number, "summary": t.get_summary_str()} for t in self.normal_queue]

        # Grouped tickets
        grouped_tickets = {}
        for t in self.all_tickets_history:
            if t.number not in grouped_tickets:
                grouped_tickets[t.number] = []
            grouped_tickets[t.number].append(t)

        ticket_statuses = []
        for num in sorted(grouped_tickets.keys()):
            tickets = grouped_tickets[num]
            state_str = self._get_ticket_state_display(tickets)

            ticket_info = {
                "number": num,
                "state": state_str,
                "details": []
            }

            for t in tickets:
                if t.is_cancelled:
                    ticket_info["details"].append({
                        "type": "cancelled",
                        "message": "Slot Burned"
                    })
                    continue

                prefix = "URGENT" if t.is_urgent else "Main"
                sub_state = "In Queue"
                if t.is_delivered:
                    sub_state = "Delivered"
                elif t.is_fully_baked():
                    sub_state = "Ready"
                elif t == self.current_baker_task:
                    sub_state = "Preparing"
                elif t.baking_count > 0:
                    sub_state = "Baking"

                detail = {
                    "type": prefix,
                    "summary": t.get_summary_str(),
                    "state": sub_state,
                }

                # Add baking breads info
                baking_breads = [str(b) for b in t.breads if b.state == BreadState.BAKING.value]
                if baking_breads:
                    detail["oven"] = baking_breads

                ticket_info["details"].append(detail)

            ticket_statuses.append(ticket_info)

        return {
            "current_task": current_task,
            "queues": {
                "urgent": urgent_list,
                "normal": normal_list,
            },
            "tickets": ticket_statuses,
            "system_info": {
                "parity_determined": self.parity_determined,
                "single_parity": self.single_parity,
                "multi_parity": self.multi_parity,
                "consumed_numbers": sorted(list(self.consumed_numbers)),
            }
        }

    def check_and_update_bread_states(self) -> List[int]:
        """
        Check all baking breads and update state to READY if done.
        Returns list of ticket numbers that have newly ready breads.
        """
        newly_ready_tickets = []
        for ticket in self.all_tickets_history:
            if ticket.is_cancelled or ticket.is_delivered:
                continue

            had_ready_update = False
            for bread in ticket.breads:
                if bread.state == BreadState.BAKING.value:
                    if bread.check_if_ready(self.baking_time_seconds):
                        had_ready_update = True

            if had_ready_update and ticket.is_fully_baked():
                newly_ready_tickets.append(ticket.number)

        return newly_ready_tickets

    def to_dict(self) -> Dict[str, Any]:
        """Serialize system state to dict"""
        return {
            "baking_time_seconds": self.baking_time_seconds,
            "all_tickets_history": [t.to_dict() for t in self.all_tickets_history],
            "normal_queue": [t.number for t in self.normal_queue],
            "urgent_queue": [t.number for t in self.urgent_queue],
            "consumed_numbers": sorted(list(self.consumed_numbers)),
            "parity_determined": self.parity_determined,
            "single_parity": self.single_parity,
            "multi_parity": self.multi_parity,
            "active_normal_ticket": self.active_normal_ticket.number if self.active_normal_ticket else None,
            "current_baker_task": self.current_baker_task.number if self.current_baker_task else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BakeryQueueSystem":
        """Deserialize system state from dict"""
        system = cls(baking_time_seconds=data.get("baking_time_seconds", 600))

        # Rebuild tickets
        ticket_map = {}
        for t_data in data.get("all_tickets_history", []):
            ticket = Ticket.from_dict(t_data)
            system.all_tickets_history.append(ticket)
            ticket_map[ticket.number] = ticket

        # Rebuild queues (reference same objects)
        for num in data.get("normal_queue", []):
            if num in ticket_map:
                system.normal_queue.append(ticket_map[num])

        for num in data.get("urgent_queue", []):
            if num in ticket_map:
                system.urgent_queue.append(ticket_map[num])

        system.consumed_numbers = set(data.get("consumed_numbers", []))
        system.parity_determined = data.get("parity_determined", False)
        system.single_parity = data.get("single_parity")
        system.multi_parity = data.get("multi_parity")

        active_normal = data.get("active_normal_ticket")
        if active_normal and active_normal in ticket_map:
            system.active_normal_ticket = ticket_map[active_normal]

        current_task = data.get("current_baker_task")
        if current_task and current_task in ticket_map:
            system.current_baker_task = ticket_map[current_task]

        return system
