"""F-04 Hamper and pickup management.

Requirements: FR-10, FR-20 to FR-26, FR-46 to FR-51, NFR-16, NFR-21, NFR-22.
Use cases: UC-05 (generate hamper), UC-06 (book pickup), UC-09 (confirm
collection), UC-17 (manage allocation rules).

Hamper life cycle:
    suggested --confirm--> confirmed --pack--> packed --collect--> collected
        |                      |                  |
        +--cancel--> cancelled +----return------> returned
"""

from __future__ import annotations

import math
import random
from datetime import date

from ..clock import Clock
from ..errors import NotFoundError, ValidationError
from ..models import (
    AllocationRule, BatchStatus, Client, Hamper, HamperLine, HamperStatus,
    Item, MovementKind, Pickup, PickupStatus, StockBatch, StockMovement, Task,
    TaskKind, TaskStatus, UnmetRequest, User, Visit,
)
from ..repository import JsonRepository
from ..security import AccessControl, AuditService
from ..text import load_text, t

# Units counted in whole pieces; quantities in these units are rounded down.
COUNT_UNITS = {"tin", "pack", "bottle", "jar", "box", "carton", "item"}
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # no I or O, which look like 1 and 0


# ----------------------------------------------------------------------------
# Pure decision functions (no data access) - easy to unit test (MC/DC)
# ----------------------------------------------------------------------------

def is_item_eligible(item: Item, client: Client) -> bool:
    """Table 15 eligibility rule (FR-20, FR-21).

    An item is eligible only if
      (A) none of its allergens matches a household allergy, AND
      (B) it carries every dietary, religious and medical tag the household needs.
    """
    no_allergen_match = not set(item.allergens) & set(client.allergies)
    meets_requirements = set(client.requirement_tags()) <= set(item.dietary_tags)
    return no_allergen_match and meets_requirements


def is_batch_usable(batch: StockBatch, today: date) -> bool:
    """A batch can be allocated if it is available, has stock and is in date."""
    return (batch.status == BatchStatus.AVAILABLE
            and batch.available > 0
            and date.fromisoformat(batch.expiry) >= today)


def required_quantity(rule: AllocationRule, client: Client) -> float:
    """Quantity of one category for one household (Table 15 quantity basis).

    Older members are allocated as adults. Count units round down to whole
    pieces so every household is treated the same way.
    """
    qty = (rule.per_adult * (client.adults + client.older)
           + rule.per_child * client.children
           + rule.per_infant * client.infants)
    if rule.unit in COUNT_UNITS:
        return float(math.floor(qty + 1e-9))
    return round(qty, 2)


def need_label(rule: AllocationRule, client: Client) -> str:
    """Need category recorded with an unmet request (FR-46)."""
    if rule.per_infant and not rule.per_adult and not rule.per_child:
        return "infant needs"
    tags = client.requirement_tags() + [f"{a}-free" for a in client.allergies]
    return ", ".join(tags) if tags else "general"


def hamper_tags(client: Client) -> list[str]:
    """Non-identifying tags a packer may see, e.g. halal, nut-free, infant (FR-30)."""
    tags = client.requirement_tags() + [f"{a}-free" for a in sorted(client.allergies)]
    if client.infants:
        tags.append("infant")
    return tags


class HamperService:
    def __init__(self, repo: JsonRepository, clock: Clock, access: AccessControl,
                 audit: AuditService, rng: random.Random | None = None):
        self.repo = repo
        self.clock = clock
        self.access = access
        self.audit = audit
        self.rng = rng or random.Random()

    # =========================================================== rules
    def list_rules(self, user: User) -> list[AllocationRule]:
        self.access.require(user, "rules.read", "rules")
        return sorted(self.repo.all("rules"), key=lambda r: r.id)

    def save_rules(self, user: User, rules: list[dict]) -> list[AllocationRule]:
        """Replace the whole rule set after validating it (FR-50, AC-49).

        If anything is wrong the old rules stay in force.
        """
        self.access.require(user, "rules.write", "rules")
        new_rules = []
        seen = set()
        for raw in rules:
            category = str(raw.get("id", "")).strip()
            if not category:
                raise ValidationError("Every rule needs a food category.", "id")
            if category in seen:
                raise ValidationError(f"'{category}' has more than one rule.", "id")
            seen.add(category)
            values = {}
            for key in ("per_adult", "per_child", "per_infant"):
                try:
                    values[key] = float(raw.get(key, 0) or 0)
                except (TypeError, ValueError):
                    raise ValidationError(f"{category}: '{key}' must be a number.", key)
                if values[key] < 0:
                    raise ValidationError(f"{category}: quantities cannot be negative.", key)
            unit = str(raw.get("unit", "")).strip()
            if not unit:
                raise ValidationError(f"{category}: enter a unit.", "unit")
            new_rules.append(AllocationRule(id=category, unit=unit, **values))

        used_categories = {i.category for i in self.repo.all("items")}
        missing = sorted(used_categories - seen)
        if missing:
            raise ValidationError("These food categories have no rule: " + ", ".join(missing), "id")

        self.repo.replace_all("rules", new_rules)
        self.audit.record(user.id, "update", "rules", detail=f"{len(new_rules)} rules saved")
        return new_rules

    # =========================================================== pickups
    def book_pickup(self, user: User, client_id: str, pickup_date, slot: str) -> Pickup:
        """Book a time slot and create a non-identifying code (FR-24, UC-06)."""
        self.access.require(user, "pickup.write", f"client:{client_id}")
        client = self.repo.get("clients", client_id)
        if client is None or client.deleted:
            raise NotFoundError(f"Client {client_id} not found.")
        try:
            day = pickup_date if isinstance(pickup_date, date) else date.fromisoformat(str(pickup_date))
        except ValueError:
            raise ValidationError("Enter the date as YYYY-MM-DD.", "date")
        if day < self.clock.today():
            raise ValidationError("A pickup cannot be booked in the past.", "date")
        if slot not in self.repo.settings["pickup_slots"]:
            raise ValidationError("Choose one of the available time slots.", "slot")
        for p in self.repo.find("pickups", client_id=client_id, status=PickupStatus.BOOKED):
            if p.date == day.isoformat():
                raise ValidationError("This household already has a pickup booked that day.", "date")

        pickup = Pickup(id=self.repo.next_id("pickups"), client_id=client_id,
                        date=day.isoformat(), slot=slot, code=self._new_code(),
                        booked_at=self.clock.now().isoformat())
        self.repo.add("pickups", pickup)
        self.audit.record(user.id, "create", f"pickup:{pickup.id}", detail=f"client:{client_id}")
        return pickup

    def _new_code(self) -> str:
        """A code like 'K27' that no other booked pickup is using."""
        in_use = {p.code for p in self.repo.all("pickups") if p.status == PickupStatus.BOOKED}
        for _ in range(1000):
            code = f"{self.rng.choice(CODE_LETTERS)}{self.rng.randint(10, 99)}"
            if code not in in_use:
                return code
        raise ValidationError("No pickup codes are free. Close old pickups first.", "code")

    def find_booked_pickup(self, code: str) -> Pickup:
        code = (code or "").strip().upper()
        for p in self.repo.all("pickups"):
            if p.code == code and p.status == PickupStatus.BOOKED:
                return p
        raise NotFoundError(t("code_not_found"))

    def pickup_slots(self) -> list[str]:
        return list(self.repo.settings["pickup_slots"])

    def client_pickups(self, user: User, client_id: str) -> list[Pickup]:
        """Pickups for one household, newest first (shown on the household page)."""
        self.access.require(user, "pickup.write", f"client:{client_id}")
        return sorted(self.repo.find("pickups", client_id=client_id),
                      key=lambda p: (p.date, p.booked_at), reverse=True)

    def list_pickups(self, user: User, day: date | None = None) -> list[Pickup]:
        self.access.require(user, "pickup.write", "pickups")
        pickups = self.repo.all("pickups")
        if day:
            pickups = [p for p in pickups if p.date == day.isoformat()]
        return sorted(pickups, key=lambda p: (p.date, p.slot, p.booked_at))

    def pickup_message(self, user: User, pickup_id: str) -> dict:
        """Message text for staff to send (FR-25, AC-20, AC-43).

        Only the date, time slot and code are included, because the message
        may be read on a shared phone. If there is no translation for the
        client's language, plain English is used.
        """
        self.access.require(user, "pickup.write", f"pickup:{pickup_id}")
        pickup = self.repo.get("pickups", pickup_id)
        if pickup is None:
            raise NotFoundError("Pickup not found.")
        client = self.repo.get("clients", pickup.client_id)
        templates = load_text()["pickup_message"]
        language = client.preferred_language if client.preferred_language in templates else "English"
        text = templates[language].format(date=pickup.date, slot=pickup.slot, code=pickup.code)
        return {"method": client.contact_method, "language": language, "text": text}

    # =========================================================== hampers
    def generate_hamper(self, user: User, pickup_code: str) -> Hamper:
        """Suggest a hamper for the household with this pickup code (UC-05).

        For every allocation rule: work out the quantity (FR-22), keep only
        items that are safe and suitable for the household (FR-20, FR-21),
        and take stock first-expiry-first-out (FR-10). Any shortfall is shown
        on the hamper and recorded as an unmet request when confirmed (FR-46).
        Same inputs always give the same hamper (NFR-21).
        """
        self.access.require(user, "hamper.write", "hampers")
        pickup = self.find_booked_pickup(pickup_code)
        if pickup.hamper_id:
            existing = self.repo.get("hampers", pickup.hamper_id)
            if existing and existing.status != HamperStatus.CANCELLED:
                raise ValidationError(f"This pickup already has hamper {existing.id}.", "pickup")
        client = self.repo.get("clients", pickup.client_id)
        today = self.clock.today()
        items = {i.id: i for i in self.repo.all("items")}

        hamper = Hamper(id=self.repo.next_id("hampers"), client_id=client.id,
                        pickup_id=pickup.id, created_by=user.id,
                        created_at=self.clock.now().isoformat())
        for rule in sorted(self.repo.all("rules"), key=lambda r: r.id):
            needed = required_quantity(rule, client)
            if needed <= 0:
                continue
            candidates = sorted(
                (b for b in self.repo.all("batches")
                 if b.category == rule.id and b.item in items
                 and is_batch_usable(b, today) and is_item_eligible(items[b.item], client)),
                key=lambda b: (b.expiry, b.id))                      # FEFO, then ID
            remaining = needed
            for batch in candidates:
                if remaining <= 0:
                    break
                take = min(remaining, batch.available)
                if rule.unit in COUNT_UNITS:
                    take = float(math.floor(take))
                if take <= 0:
                    continue
                hamper.lines.append(HamperLine(item=batch.item, category=rule.id,
                                               batch_id=batch.id, quantity=round(take, 3),
                                               unit=rule.unit, rule_text=rule.describe()))
                remaining = round(remaining - take, 3)
            if remaining > 0:
                hamper.shortfalls.append({"category": rule.id, "quantity": remaining,
                                          "unit": rule.unit, "need": need_label(rule, client),
                                          "rule_text": rule.describe()})
        self.repo.add("hampers", hamper)
        pickup.hamper_id = hamper.id
        self.repo.add("pickups", pickup)
        self.audit.record(user.id, "create", f"hamper:{hamper.id}", detail=f"pickup:{pickup.code}")
        return hamper

    def list_hampers(self, user: User, statuses=None) -> list[Hamper]:
        self.access.require(user, "hamper.write", "hampers")
        hampers = self.repo.all("hampers")
        if statuses:
            wanted = {HamperStatus(s) for s in statuses}
            hampers = [h for h in hampers if h.status in wanted]
        return sorted(hampers, key=lambda h: h.id, reverse=True)

    def hamper_for_code(self, user: User, code: str) -> Hamper | None:
        """The current hamper (if any) for a booked pickup code."""
        self.access.require(user, "hamper.write", "hampers")
        pickup = self.find_booked_pickup(code)
        hamper = self.repo.get("hampers", pickup.hamper_id) if pickup.hamper_id else None
        return hamper if hamper and hamper.status != HamperStatus.CANCELLED else None

    def pickup_code_for(self, hamper: Hamper) -> str:
        pickup = self.repo.get("pickups", hamper.pickup_id)
        return pickup.code if pickup else ""

    def get_hamper(self, user: User, hamper_id: str) -> Hamper:
        self.access.require(user, "hamper.write", f"hamper:{hamper_id}")
        return self._get(hamper_id)

    def _get(self, hamper_id: str) -> Hamper:
        hamper = self.repo.get("hampers", hamper_id)
        if hamper is None:
            raise NotFoundError(f"Hamper {hamper_id} not found.")
        return hamper

    def adjust_line(self, user: User, hamper_id: str, line_index: int,
                    new_quantity: float, reason: str) -> Hamper:
        """Staff change a suggested quantity, with an audited reason (FR-51, AC-50)."""
        self.access.require(user, "hamper.write", f"hamper:{hamper_id}")
        hamper = self._get(hamper_id)
        if hamper.status != HamperStatus.SUGGESTED:
            raise ValidationError("Only a suggested hamper can be adjusted.", "status")
        if not (reason or "").strip():
            raise ValidationError("Enter a reason for the change.", "reason")
        if not 0 <= line_index < len(hamper.lines):
            raise ValidationError("Choose a line of the hamper.", "line")
        line = hamper.lines[line_index]
        batch = self.repo.get("batches", line.batch_id)
        try:
            new_quantity = float(new_quantity)
        except (TypeError, ValueError):
            raise ValidationError("Quantity must be a number.", "quantity")
        if new_quantity < 0 or new_quantity > batch.available:
            raise ValidationError(f"Quantity must be between 0 and {batch.available:g}.", "quantity")

        hamper.adjustments.append({"item": line.item, "from": line.quantity, "to": new_quantity,
                                   "reason": reason.strip(), "by": user.id,
                                   "at": self.clock.now().isoformat()})
        if new_quantity == 0:
            hamper.lines.pop(line_index)
        else:
            line.quantity = round(new_quantity, 3)
        self.repo.add("hampers", hamper)
        self.audit.record(user.id, "adjust", f"hamper:{hamper_id}",
                          detail=f"{line.item} {hamper.adjustments[-1]['from']:g} -> {new_quantity:g}: {reason.strip()}")
        return hamper

    def confirm_hamper(self, user: User, hamper_id: str, priority: int = 2) -> Task:
        """Reserve stock, record unmet requests, create a packing task (UC-05 step 6)."""
        self.access.require(user, "hamper.write", f"hamper:{hamper_id}")
        hamper = self._get(hamper_id)
        if hamper.status != HamperStatus.SUGGESTED:
            raise ValidationError("This hamper has already been confirmed or closed.", "status")
        if not hamper.lines:
            raise ValidationError("The hamper is empty.", "lines")
        # Check every line first so nothing is reserved if one line fails
        for line in hamper.lines:
            batch = self.repo.get("batches", line.batch_id)
            if batch is None or batch.available < line.quantity:
                raise ValidationError("Stock has changed since this hamper was suggested. "
                                      "Cancel it and generate it again.", "lines")
        for line in hamper.lines:
            batch = self.repo.get("batches", line.batch_id)
            batch.reserved = round(batch.reserved + line.quantity, 3)
            self.repo.add("batches", batch)
        for gap in hamper.shortfalls:                                     # FR-46
            self._record_unmet(hamper, gap["category"], gap["need"], gap["quantity"], gap["unit"])

        pickup = self.repo.get("pickups", hamper.pickup_id)
        task = Task(id=self.repo.next_id("tasks"), kind=TaskKind.PACK,
                    title=f"Pack hamper {hamper.id} for pickup {pickup.code} ({pickup.date} {pickup.slot})",
                    created_by=user.id, created_at=self.clock.now().isoformat(),
                    priority=priority, hamper_id=hamper.id)
        self.repo.add("tasks", task)
        hamper.status = HamperStatus.CONFIRMED
        hamper.task_id = task.id
        self.repo.add("hampers", hamper)
        self.audit.record(user.id, "confirm", f"hamper:{hamper.id}", detail=f"task:{task.id}")
        return task

    def cancel_hamper(self, user: User, hamper_id: str) -> Hamper:
        """Cancel a suggested or confirmed (not yet packed) hamper (UC-05 6a)."""
        self.access.require(user, "hamper.write", f"hamper:{hamper_id}")
        hamper = self._get(hamper_id)
        if hamper.status not in (HamperStatus.SUGGESTED, HamperStatus.CONFIRMED):
            raise ValidationError("Only a suggested or confirmed hamper can be cancelled.", "status")
        if hamper.status == HamperStatus.CONFIRMED:
            self._release_reservations(hamper)
            self._close_task(hamper)
        hamper.status = HamperStatus.CANCELLED
        self.repo.add("hampers", hamper)
        pickup = self.repo.get("pickups", hamper.pickup_id)
        pickup.hamper_id = ""
        self.repo.add("pickups", pickup)
        self.audit.record(user.id, "cancel", f"hamper:{hamper.id}")
        return hamper

    # ===================================================== counter / pickup
    def counter_lookup(self, user: User, code: str) -> dict:
        """What the counter screen shows: code, slot and status only (NFR-22)."""
        self.access.require(user, "pickup.write", "pickups")
        pickup = self.find_booked_pickup(code)
        hamper = self.repo.get("hampers", pickup.hamper_id) if pickup.hamper_id else None
        return {"code": pickup.code, "date": pickup.date, "slot": pickup.slot,
                "hamper_id": hamper.id if hamper else "",
                "hamper_status": hamper.status.value if hamper else "not generated",
                "item_count": len(hamper.lines) if hamper else 0}

    def confirm_collection(self, user: User, code: str) -> dict:
        """Record collection by pickup code (FR-26) and count items as distributed (FR-47)."""
        self.access.require(user, "pickup.write", "pickups")
        pickup = self.find_booked_pickup(code)
        hamper = self.repo.get("hampers", pickup.hamper_id) if pickup.hamper_id else None
        if hamper is None or hamper.status != HamperStatus.PACKED:
            raise ValidationError("This hamper is not packed yet. Please check the packing tasks.", "code")
        today = self.clock.today().isoformat()
        for line in hamper.lines:
            self.repo.add("movements", StockMovement(
                id=self.repo.next_id("movements"), date=today, batch_id=line.batch_id,
                item=line.item, category=line.category, quantity=line.quantity,
                kind=MovementKind.DISTRIBUTED, reference=hamper.id))
        hamper.status = HamperStatus.COLLECTED
        pickup.status = PickupStatus.COLLECTED
        client = self.repo.get("clients", pickup.client_id)
        client.visits.append(Visit(date=today, hamper_id=hamper.id))            # FR-19
        self.repo.add("hampers", hamper)
        self.repo.add("pickups", pickup)
        self.repo.add("clients", client)
        self.audit.record(user.id, "collect", f"hamper:{hamper.id}", detail=f"pickup:{pickup.code}")
        return {"code": pickup.code, "hamper_id": hamper.id, "items": len(hamper.lines)}

    def return_uncollected(self, user: User, hamper_id: str) -> Hamper:
        """Put an uncollected hamper's items back into stock (FR-48, AC-47)."""
        self.access.require(user, "hamper.write", f"hamper:{hamper_id}")
        hamper = self._get(hamper_id)
        pickup = self.repo.get("pickups", hamper.pickup_id)
        if hamper.status not in (HamperStatus.CONFIRMED, HamperStatus.PACKED):
            raise ValidationError("Only a confirmed or packed hamper can be returned.", "status")
        if pickup.date > self.clock.today().isoformat():
            raise ValidationError("The pickup day has not finished yet.", "date")
        if hamper.status == HamperStatus.CONFIRMED:
            self._release_reservations(hamper)
            self._close_task(hamper)
        else:
            today = self.clock.today().isoformat()
            for line in hamper.lines:
                batch = self.repo.get("batches", line.batch_id)
                batch.quantity = round(batch.quantity + line.quantity, 3)
                self.repo.add("batches", batch)
                self.repo.add("movements", StockMovement(
                    id=self.repo.next_id("movements"), date=today, batch_id=batch.id,
                    item=batch.item, category=batch.category, quantity=line.quantity,
                    kind=MovementKind.RETURNED, reference=hamper.id))
        hamper.status = HamperStatus.RETURNED
        pickup.status = PickupStatus.RETURNED
        self.repo.add("hampers", hamper)
        self.repo.add("pickups", pickup)
        self.audit.record(user.id, "return", f"hamper:{hamper.id}")
        return hamper

    # =========================================================== helpers
    def _release_reservations(self, hamper: Hamper) -> None:
        for line in hamper.lines:
            batch = self.repo.get("batches", line.batch_id)
            if batch:
                batch.reserved = round(max(batch.reserved - line.quantity, 0.0), 3)
                self.repo.add("batches", batch)

    def _close_task(self, hamper: Hamper) -> None:
        task = self.repo.get("tasks", hamper.task_id) if hamper.task_id else None
        if task and task.status != TaskStatus.COMPLETE:
            task.status = TaskStatus.COMPLETE
            task.completed_by = "system"
            task.completed_at = self.clock.now().isoformat()
            task.title += " (cancelled)"
            self.repo.add("tasks", task)

    def _record_unmet(self, hamper: Hamper, category: str, need: str,
                      quantity: float, unit: str) -> UnmetRequest:
        unmet = UnmetRequest(id=self.repo.next_id("unmet"), client_id=hamper.client_id,
                             hamper_id=hamper.id, category=category, need=need,
                             quantity=quantity, unit=unit, date=self.clock.today().isoformat())
        self.repo.add("unmet", unmet)
        return unmet

    def safe_substitutes(self, hamper: Hamper, line: HamperLine) -> list[StockBatch]:
        """Substitutes from the same category that are safe for the household (FR-49)."""
        client = self.repo.get("clients", hamper.client_id)
        items = {i.id: i for i in self.repo.all("items")}
        today = self.clock.today()
        return sorted(
            (b for b in self.repo.all("batches")
             if b.category == line.category and b.id != line.batch_id and b.item in items
             and is_batch_usable(b, today) and b.available >= line.quantity
             and is_item_eligible(items[b.item], client)),
            key=lambda b: (b.expiry, b.id))
