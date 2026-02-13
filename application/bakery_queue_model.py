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
        self.consumed_numbers: Set[int] = set()
        self.parity_determined: bool = False
        self.single_parity: Optional[int] = None
        self.multi_parity: Optional[int] = None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _expire_old_slots(self):
        if self.current_served <= 0:
            return
        self.consumed_numbers = {n for n in self.consumed_numbers if n > self.current_served}

    def _get_global_max(self) -> int:
        return max(self.consumed_numbers) if self.consumed_numbers else 0

    def _get_threshold_number(self) -> int:
        return int(self.current_served or 0)

    def _determine_parity(self, first_type: str) -> None:
        if self.parity_determined:
            return
        if first_type == "single":
            self.single_parity = 1
            self.multi_parity = 0
        else:
            self.multi_parity = 1
            self.single_parity = 0
        self.parity_determined = True

    def _find_valid_slots(self, target_parity: int) -> List[int]:
        threshold = self._get_threshold_number()
        global_max = self._get_global_max()
        valid_slots = []
        for num in range(threshold + 1, global_max):
            if num in self.consumed_numbers:
                continue
            if int(num) % 2 == int(target_parity):
                valid_slots.append(int(num))
        return valid_slots

    def _get_next_sequence_number(self, target_parity: int) -> int:
        start_point = max(self._get_global_max(), self._get_threshold_number())
        search = start_point + 1
        while search % 2 != int(target_parity):
            search += 1
        return int(search)

    def issue_single(self):
        self._expire_old_slots()
        if not self.parity_determined:
            self._determine_parity("single")
        target_parity = int(self.single_parity or 0)

        valid_slots = self._find_valid_slots(target_parity)
        if valid_slots:
            assigned = int(valid_slots[0])
        else:
            assigned = self._get_next_sequence_number(target_parity)

        self.consumed_numbers.add(int(assigned))
        self.next_number = max(int(self.next_number), int(assigned) + 1)

        t = Ticket(number=assigned, kind="single", quantity=1, timestamp=self._now_iso())
        self.tickets[assigned] = t
        return t

    def issue_multi(self, quantity: int):
        if quantity < 2:
            raise ValueError("quantity must be >= 2 for multi")

        self._expire_old_slots()
        if not self.parity_determined:
            self._determine_parity("multi")
        target_parity = int(self.multi_parity or 0)

        valid_slots = self._find_valid_slots(target_parity)
        if len(valid_slots) >= quantity:
            consumed = valid_slots[:quantity]
            ticket_number = int(consumed[-1])
            for s in consumed[:-1]:
                ph = Ticket(
                    number=int(s),
                    kind="consumed",
                    quantity=0,
                    timestamp=self._now_iso(),
                    status="consumed",
                    parent_ticket=ticket_number,
                )
                self.tickets[int(s)] = ph
            for s in consumed:
                self.consumed_numbers.add(int(s))
            self.next_number = max(int(self.next_number), int(ticket_number) + 1)
            t = Ticket(number=ticket_number, kind="multi", quantity=quantity, timestamp=self._now_iso())
            self.tickets[ticket_number] = t
            return t

        assigned = self._get_next_sequence_number(target_parity)
        self.consumed_numbers.add(int(assigned))
        self.next_number = max(int(self.next_number), int(assigned) + 1)

        t = Ticket(number=assigned, kind="multi", quantity=quantity, timestamp=self._now_iso())
        self.tickets[assigned] = t
        return t

    def to_dict(self) -> Dict:
        return {
            "tickets": {str(n): asdict(t) for n, t in self.tickets.items()},
            "next_number": self.next_number,
            "current_served": self.current_served,
            "consumed_numbers": sorted(list(self.consumed_numbers)),
            "parity_determined": bool(self.parity_determined),
            "single_parity": self.single_parity,
            "multi_parity": self.multi_parity,
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
        consumed = data.get("consumed_numbers")
        if consumed is None:
            # Backward compatibility for old snapshots.
            consumed = list(data.get("slots_for_multis", [])) + list(data.get("slots_for_singles", []))
            consumed += list(inst.tickets.keys())
        inst.consumed_numbers = set(int(x) for x in consumed)

        inst.parity_determined = bool(data.get("parity_determined", False))
        inst.single_parity = data.get("single_parity")
        inst.multi_parity = data.get("multi_parity")

        if not inst.parity_determined:
            singles = [n for n, t in inst.tickets.items() if t.kind == "single"]
            multis = [n for n, t in inst.tickets.items() if t.kind == "multi"]
            if singles:
                inst.single_parity = int(singles[0]) % 2
                inst.multi_parity = 1 - int(inst.single_parity)
                inst.parity_determined = True
            elif multis:
                inst.multi_parity = int(multis[0]) % 2
                inst.single_parity = 1 - int(inst.multi_parity)
                inst.parity_determined = True

        if inst.next_number <= inst._get_global_max():
            inst.next_number = inst._get_global_max() + 1
        return inst
