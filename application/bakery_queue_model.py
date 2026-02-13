from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Optional, Set


@dataclass
class Ticket:
    number: int
    kind: str  # 'single', 'multi', or 'consumed'
    quantity: int
    timestamp: str
    status: str = "waiting"
    served_at: Optional[str] = None
    parent_ticket: Optional[int] = None


class BakeryQueueState:
    """Parity-based ticket allocator.

    The first real ticket determines parity mapping:
    - if first is single: singles=odd, multis=even
    - if first is multi: multis=odd, singles=even

    Multi tickets can consume multiple same-parity slots and get the last slot as
    the visible ticket number. Previous consumed slots are stored as placeholder
    tickets (kind='consumed').
    """

    def __init__(self):
        self.tickets: Dict[int, Ticket] = {}
        self.current_served: int = 0

        self.consumed_numbers: Set[int] = set()
        self.parity_determined: bool = False
        self.single_parity: Optional[int] = None
        self.multi_parity: Optional[int] = None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _determine_parity(self, first_kind: str) -> None:
        if self.parity_determined:
            return
        if first_kind == "single":
            self.single_parity, self.multi_parity = 1, 0
        else:
            self.single_parity, self.multi_parity = 0, 1
        self.parity_determined = True

    def _get_global_max(self) -> int:
        return max(self.consumed_numbers) if self.consumed_numbers else 0

    def _get_threshold_number(self) -> int:
        return int(self.current_served or 0)

    def _find_valid_slots(self, target_parity: int):
        threshold = self._get_threshold_number()
        global_max = self._get_global_max()
        return [
            n
            for n in range(threshold + 1, global_max)
            if n not in self.consumed_numbers and n % 2 == target_parity
        ]

    def _get_next_sequence_number(self, target_parity: int) -> int:
        start_point = max(self._get_global_max(), self._get_threshold_number())
        search = start_point + 1
        while search % 2 != target_parity:
            search += 1
        return search

    def _assign_ticket(self, kind: str, quantity: int) -> Ticket:
        self._determine_parity(kind)
        target_parity = self.single_parity if kind == "single" else self.multi_parity
        valid_slots = self._find_valid_slots(int(target_parity))

        if kind == "single":
            consumed_slots = [valid_slots[0]] if valid_slots else [self._get_next_sequence_number(int(target_parity))]
        else:
            if len(valid_slots) >= int(quantity):
                consumed_slots = valid_slots[: int(quantity)]
            else:
                consumed_slots = [self._get_next_sequence_number(int(target_parity))]

        assigned = int(consumed_slots[-1])
        for slot in consumed_slots:
            self.consumed_numbers.add(int(slot))

        if kind == "multi" and len(consumed_slots) > 1:
            for slot in consumed_slots[:-1]:
                self.tickets[int(slot)] = Ticket(
                    number=int(slot),
                    kind="consumed",
                    quantity=0,
                    timestamp=self._now_iso(),
                    status="consumed",
                    parent_ticket=assigned,
                )

        t = Ticket(number=assigned, kind=kind, quantity=int(quantity), timestamp=self._now_iso())
        self.tickets[assigned] = t
        return t

    def issue_single(self):
        return self._assign_ticket("single", 1)

    def issue_multi(self, quantity: int):
        if quantity < 2:
            raise ValueError("quantity must be >= 2 for multi")
        return self._assign_ticket("multi", int(quantity))

    def to_dict(self) -> Dict:
        return {
            "tickets": {str(n): asdict(t) for n, t in self.tickets.items()},
            "current_served": self.current_served,
            "consumed_numbers": sorted(list(self.consumed_numbers)),
            "parity_determined": self.parity_determined,
            "single_parity": self.single_parity,
            "multi_parity": self.multi_parity,
        }

    def mark_ticket_served(self, ticket_id: int) -> None:
        t = self.tickets.get(ticket_id)
        if not t or t.kind not in ("single", "multi") or t.status == "served":
            return
        if ticket_id <= self.current_served:
            return
        t.status = "served"
        t.served_at = self._now_iso()
        self.current_served = ticket_id

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

        inst.current_served = int(data.get("current_served", 0) or 0)

        consumed = data.get("consumed_numbers")
        if consumed is None:
            # Backward compatibility for old snapshots.
            consumed = list(inst.tickets.keys())
        inst.consumed_numbers = set(int(x) for x in consumed)

        inst.parity_determined = bool(data.get("parity_determined", False))
        inst.single_parity = data.get("single_parity")
        inst.multi_parity = data.get("multi_parity")

        if not inst.parity_determined:
            real_tickets = sorted(
                [t for t in inst.tickets.values() if t.kind in ("single", "multi")],
                key=lambda x: x.number,
            )
            if real_tickets:
                inst._determine_parity(real_tickets[0].kind)

        return inst