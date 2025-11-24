from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Set


@dataclass
class Ticket:
    number: int
    kind: str  # 'single', 'multi', or 'consumed'
    quantity: int  # for multi; single=1; consumed=0
    timestamp: str
    status: str = "waiting"  # 'waiting', 'served', or 'consumed'
    served_at: Optional[str] = None
    parent_ticket: Optional[int] = None


class BakeryQueueState:
    def __init__(self):
        self.tickets: Dict[int, Ticket] = {}
        self.next_number: int = 1
        self.current_served: int = 0
        self.slots_for_multis: Set[int] = set()
        self.slots_for_singles: Set[int] = set()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _expire_old_slots(self):
        if self.current_served <= 0:
            return
        self.slots_for_multis = {n for n in self.slots_for_multis if n > self.current_served}
        self.slots_for_singles = {n for n in self.slots_for_singles if n > self.current_served}

    def _prev_ticket_of_kind(self, kind: str) -> Optional[int]:
        nums = [n for n, t in self.tickets.items() if t.kind == kind]
        return max(nums) if nums else None

    def issue_single(self):
        self._expire_old_slots()
        if self.next_number <= self.current_served:
            self.next_number = self.current_served + 1

        valid_slots = sorted(n for n in self.slots_for_singles if n > self.current_served)
        if valid_slots:
            s = valid_slots[0]
            self.slots_for_singles.discard(s)
            t = Ticket(number=s, kind="single", quantity=1, timestamp=self._now_iso())
            self.tickets[s] = t
            return t

        candidate = self.next_number
        prev_single = self._prev_ticket_of_kind("single")
        if prev_single is not None and prev_single == candidate - 1:
            if candidate not in self.tickets:
                self.slots_for_multis.add(candidate)
            assigned = candidate + 1
            self.next_number = assigned + 1
        else:
            assigned = candidate
            self.next_number = candidate + 1

        t = Ticket(number=assigned, kind="single", quantity=1, timestamp=self._now_iso())
        self.tickets[assigned] = t
        return t

    def issue_multi(self, quantity: int):
        if quantity < 2:
            raise ValueError("quantity must be >= 2 for multi")

        self._expire_old_slots()
        if self.next_number <= self.current_served:
            self.next_number = self.current_served + 1

        available = sorted(n for n in self.slots_for_multis if n > self.current_served)
        if len(available) >= quantity:
            consumed = available[:quantity]
            for s in consumed:
                self.slots_for_multis.discard(s)
            ticket_number = consumed[-1]
            for s in consumed[:-1]:
                ph = Ticket(
                    number=s,
                    kind="consumed",
                    quantity=0,
                    timestamp=self._now_iso(),
                    status="consumed",
                    parent_ticket=ticket_number,
                )
                self.tickets[s] = ph
            t = Ticket(number=ticket_number, kind="multi", quantity=quantity, timestamp=self._now_iso())
            self.tickets[ticket_number] = t
            return t

        candidate = self.next_number
        prev_multi = self._prev_ticket_of_kind("multi")
        if prev_multi is not None and prev_multi == candidate - 1:
            if candidate not in self.tickets:
                self.slots_for_singles.add(candidate)
            assigned = candidate + 1
            self.next_number = assigned + 1
        else:
            assigned = candidate
            self.next_number = candidate + 1

        t = Ticket(number=assigned, kind="multi", quantity=quantity, timestamp=self._now_iso())
        self.tickets[assigned] = t
        return t

    def to_dict(self) -> Dict:
        return {
            "tickets": {str(n): asdict(t) for n, t in self.tickets.items()},
            "next_number": self.next_number,
            "current_served": self.current_served,
            "slots_for_multis": sorted(list(self.slots_for_multis)),
            "slots_for_singles": sorted(list(self.slots_for_singles)),
        }


    def mark_ticket_served(self, ticket_id: int) -> None:
        t = self.tickets.get(ticket_id)
        if not t:
            return
        if t.kind not in ("single", "multi"):
            return
        if t.status == "served":
            return
        if ticket_id <= self.current_served:
            return

        t.status = "served"
        t.served_at = self._now_iso()
        self.current_served = ticket_id
        self._expire_old_slots()


    @classmethod
    def from_dict(cls, data: Dict) -> "BakeryQueueState":
        inst = cls()
        tickets = data.get("tickets", {})
        for k, v in tickets.items():
            n = int(k)
            inst.tickets[n] = Ticket(
                number=n,
                kind=v["kind"],
                quantity=v.get("quantity", 0),
                timestamp=v.get("timestamp", ""),
                status=v.get("status", "waiting"),
                served_at=v.get("served_at"),
                parent_ticket=v.get("parent_ticket"),
            )
        inst.next_number = data.get("next_number", 1)
        inst.current_served = data.get("current_served", 0)
        inst.slots_for_multis = set(data.get("slots_for_multis", []))
        inst.slots_for_singles = set(data.get("slots_for_singles", []))
        return inst
