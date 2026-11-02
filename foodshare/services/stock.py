"""F-01 Donation management and F-02 Stock and expiry management.

Requirements: FR-01 to FR-12. Use cases: UC-01, UC-02, UC-03, UC-10.
"""

from __future__ import annotations

from datetime import date, timedelta

from ..clock import Clock
from ..errors import NotFoundError, ValidationError
from ..models import (
    BatchStatus, Donation, DonationLine, DonorOffer, Item, MovementKind,
    StockBatch, StockMovement, User,
)
from ..repository import JsonRepository
from ..security import AccessControl, AuditService
from ..text import t

STORAGE_TYPES = ("ambient", "chilled", "frozen")
FLAG_REASONS = ("damaged", "expired", "missing")


def parse_date(value, field: str) -> date:
    """Accept a date object or 'YYYY-MM-DD' text; raise a clear error otherwise."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValidationError("Enter the date as YYYY-MM-DD, for example 2026-11-30.", field)


def parse_quantity(value, field: str = "quantity") -> float:
    """Quantities must be positive numbers (UC-01 flow 3b)."""
    try:
        qty = float(value)
    except (TypeError, ValueError):
        raise ValidationError("Quantity must be a number.", field)
    if qty <= 0:
        raise ValidationError("Quantity must be more than zero.", field)
    return round(qty, 3)


class StockService:
    def __init__(self, repo: JsonRepository, clock: Clock, access: AccessControl, audit: AuditService):
        self.repo = repo
        self.clock = clock
        self.access = access
        self.audit = audit

    # ----------------------------------------------------------- catalogue
    def get_item(self, name: str) -> Item:
        item = self.repo.get("items", name)
        if item is None:
            raise NotFoundError(f"'{name}' is not in the item catalogue.")
        return item

    def list_items(self) -> list[Item]:
        return sorted(self.repo.all("items"), key=lambda i: (i.category, i.id))

    def add_item(self, user: User, name: str, category: str, unit: str,
                 allergens=(), dietary_tags=(), essential=False, min_level=0.0) -> Item:
        """Add a new kind of food to the catalogue, with its safety tags."""
        self.access.require(user, "stock.configure", "items")
        name, category, unit = name.strip(), category.strip(), unit.strip()
        if not name or not category or not unit:
            raise ValidationError("Item name, category and unit are all required.", "name")
        if self.repo.get("items", name):
            raise ValidationError(f"'{name}' is already in the catalogue.", "name")
        rule = self.repo.get("rules", category)
        if rule is None:
            raise ValidationError(f"'{category}' has no allocation rule. Ask an Admin to add one.", "category")
        if unit != rule.unit:
            raise ValidationError(f"Items in {category} are counted in '{rule.unit}'.", "unit")
        item = Item(id=name, category=category, unit=unit, allergens=sorted(set(allergens)),
                    dietary_tags=sorted(set(dietary_tags)), essential=essential,
                    min_level=float(min_level))
        self.repo.add("items", item)
        self.audit.record(user.id, "create", f"item:{name}")
        return item

    def set_essential(self, user: User, name: str, essential: bool, min_level: float) -> Item:
        """Mark an item essential with a minimum level for alerts (FR-11)."""
        self.access.require(user, "stock.configure", f"item:{name}")
        item = self.get_item(name)
        if min_level < 0:
            raise ValidationError("Minimum level cannot be negative.", "min_level")
        item.essential, item.min_level = essential, float(min_level)
        self.repo.add("items", item)
        return item

    # ----------------------------------------------------------- donations
    def record_donation(self, user: User, lines: list[dict],
                        donor_name: str = "", donor_contact: str = "") -> Donation:
        """Record a received donation and add each line to stock (FR-01, FR-07).

        ``lines`` is a list of {item, quantity, expiry, storage}. All lines are
        validated before anything is saved, so a bad line never leaves a
        half-recorded donation. Donor details are optional (FR-06).
        """
        self.access.require(user, "donation.write", "donations")
        if not lines:
            raise ValidationError("Add at least one item.", "lines")
        today = self.clock.today()

        checked = []
        for line in lines:
            item = self.get_item(line.get("item", ""))
            qty = parse_quantity(line.get("quantity"))
            expiry = parse_date(line.get("expiry"), "expiry")
            if expiry < today:                                   # FR-04
                raise ValidationError(f"{item.id}: {t('expired_donation')}", "expiry")
            storage = (line.get("storage") or "").lower()
            if storage not in STORAGE_TYPES:
                raise ValidationError("Storage must be ambient, chilled or frozen.", "storage")
            checked.append((item, qty, expiry, storage))

        donation = Donation(id=self.repo.next_id("donations"), received_on=today.isoformat(),
                            recorded_by=user.id, donor_name=donor_name.strip(),
                            donor_contact=donor_contact.strip())
        for item, qty, expiry, storage in checked:
            batch = StockBatch(id=self.repo.next_id("batches"), item=item.id,
                               category=item.category, quantity=qty, unit=item.unit,
                               expiry=expiry.isoformat(), storage=storage,
                               received_on=today.isoformat(), donation_id=donation.id)
            self.repo.add("batches", batch)
            self._move(batch, qty, MovementKind.RECEIVED, donation.id)
            donation.lines.append(DonationLine(item=item.id, quantity=qty, unit=item.unit,
                                               expiry=batch.expiry, storage=storage,
                                               batch_id=batch.id))
        self.repo.add("donations", donation)
        self.audit.record(user.id, "create", f"donation:{donation.id}")
        return donation

    def confirmation_text(self, donation: Donation) -> str:
        """Donation confirmation with reference, date and items (FR-05)."""
        rows = [f"  - {l.item}: {l.quantity:g} {l.unit} (use by {l.expiry})" for l in donation.lines]
        return "\n".join([
            "FoodShare Tracker - donation received. Thank you!",
            f"Reference: {donation.id}",
            f"Date: {donation.received_on}",
            "Items:",
            *rows,
        ])

    def priority_needs(self) -> dict:
        """Priority-needs list and guidance shown on the donor offer form (FR-03)."""
        return {"needs": list(self.repo.settings["priority_needs"]),
                "guidance": t("donation_guidance")}

    def set_priority_needs(self, user: User, needs: list[str]) -> None:
        self.access.require(user, "stock.configure", "settings")
        self.repo.settings["priority_needs"] = [n.strip() for n in needs if n.strip()]
        self.repo.save_settings()

    def record_offer(self, user: User, donor_name: str, items: list[dict],
                     dropoff_time: str = "") -> DonorOffer:
        """Record a donor's offer before the food arrives (FR-02)."""
        self.access.require(user, "donation.write", "offers")
        if not donor_name.strip():
            raise ValidationError("Enter the donor's name or organisation.", "donor_name")
        if not items:
            raise ValidationError("Add at least one offered item.", "items")
        clean = []
        for entry in items:
            if not str(entry.get("item", "")).strip():
                raise ValidationError("Each offered item needs a name.", "item")
            clean.append({"item": entry["item"].strip(),
                          "quantity": parse_quantity(entry.get("quantity")),
                          "expiry": parse_date(entry.get("expiry"), "expiry").isoformat()})
        offer = DonorOffer(id=self.repo.next_id("offers"),
                           recorded_on=self.clock.today().isoformat(), recorded_by=user.id,
                           donor_name=donor_name.strip(), items=clean,
                           dropoff_time=dropoff_time.strip())
        self.repo.add("offers", offer)
        self.audit.record(user.id, "create", f"offer:{offer.id}")
        return offer

    def list_offers(self, user: User) -> list[DonorOffer]:
        self.access.require(user, "donation.read", "offers")
        return sorted(self.repo.all("offers"), key=lambda o: o.id, reverse=True)

    # --------------------------------------------------------------- stock
    def list_batches(self, user: User, include_unavailable: bool = False) -> list[StockBatch]:
        self.access.require(user, "stock.read", "stock")
        batches = self.repo.all("batches")
        if not include_unavailable:
            batches = [b for b in batches if b.status == BatchStatus.AVAILABLE and b.quantity > 0]
        return sorted(batches, key=lambda b: (b.item, b.expiry, b.id))

    def stock_summary(self, user: User) -> list[dict]:
        """Totals by item: quantity, available and nearest expiry (FR-08)."""
        self.access.require(user, "stock.read", "stock")
        summary: dict[str, dict] = {}
        for item in self.repo.all("items"):
            summary[item.id] = {"item": item.id, "category": item.category, "unit": item.unit,
                                "on_hand": 0.0, "available": 0.0, "nearest_expiry": None,
                                "essential": item.essential, "min_level": item.min_level}
        for b in self.repo.all("batches"):
            if b.status != BatchStatus.AVAILABLE or b.quantity <= 0 or b.item not in summary:
                continue
            row = summary[b.item]
            row["on_hand"] = round(row["on_hand"] + b.quantity, 3)
            row["available"] = round(row["available"] + b.available, 3)
            if row["nearest_expiry"] is None or b.expiry < row["nearest_expiry"]:
                row["nearest_expiry"] = b.expiry
        return sorted(summary.values(), key=lambda r: (r["category"], r["item"]))

    def expiring_soon(self, user: User, days: int | None = None) -> list[StockBatch]:
        """Batches expiring within the threshold, inclusive (FR-09, AC-07)."""
        self.access.require(user, "stock.read", "stock")
        days = self.repo.settings["expiry_threshold_days"] if days is None else days
        if days < 0:
            raise ValidationError("The number of days cannot be negative.", "days")
        limit = self.clock.today() + timedelta(days=days)
        return [b for b in self.list_batches(user) if date.fromisoformat(b.expiry) <= limit]

    def set_expiry_threshold(self, user: User, days: int) -> None:
        self.access.require(user, "stock.configure", "settings")
        if not isinstance(days, int) or days < 0 or days > 90:
            raise ValidationError("Choose a whole number of days from 0 to 90.", "days")
        self.repo.settings["expiry_threshold_days"] = days
        self.repo.save_settings()

    def low_stock_alerts(self, user: User) -> list[dict]:
        """Essential items whose available quantity is below the minimum (FR-11, AC-09)."""
        return [row for row in self.stock_summary(user)
                if row["essential"] and row["available"] < row["min_level"]]

    def flag_batch(self, user: User, batch_id: str, reason: str, note: str = "") -> StockBatch:
        """Remove a damaged, expired or missing batch from stock (FR-12, UC-03)."""
        self.access.require(user, "stock.flag", f"batch:{batch_id}")
        batch = self.repo.get("batches", batch_id)
        if batch is None:
            raise NotFoundError(f"Batch {batch_id} not found.")
        reason = (reason or "").strip().lower()
        if reason not in FLAG_REASONS:
            raise ValidationError("Choose a reason: damaged, expired or missing.", "reason")
        if batch.status != BatchStatus.AVAILABLE:
            raise ValidationError(f"Batch {batch_id} is already flagged as {batch.status.value}.", "batch")
        if batch.reserved > 0:
            raise ValidationError(
                "This batch is reserved for a hamper. Flag it from the packing task so a "
                "safe substitute can be chosen.", "batch")
        discarded = batch.quantity
        batch.status = BatchStatus(reason)
        batch.flag_reason = f"{reason}: {note.strip()}" if note.strip() else reason
        batch.flagged_by = user.id
        batch.flagged_at = self.clock.now().isoformat()
        self.repo.add("batches", batch)
        self._move(batch, discarded, MovementKind.DISCARDED, f"flag:{batch.id}")
        self.audit.record(user.id, "flag", f"batch:{batch.id}", detail=batch.flag_reason)
        return batch

    def flagged_batches(self, user: User) -> list[StockBatch]:
        self.access.require(user, "stock.read", "stock")
        return [b for b in self.repo.all("batches") if b.status != BatchStatus.AVAILABLE]

    # ------------------------------------------------------------- helpers
    def _move(self, batch: StockBatch, qty: float, kind: MovementKind, reference: str) -> None:
        """Record a stock movement for the stock movement report (FR-37)."""
        self.repo.add("movements", StockMovement(
            id=self.repo.next_id("movements"), date=self.clock.today().isoformat(),
            batch_id=batch.id, item=batch.item, category=batch.category,
            quantity=round(qty, 3), kind=kind, reference=reference))
