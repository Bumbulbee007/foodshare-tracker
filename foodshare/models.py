"""Domain model (entity classes) for FoodShare Tracker.

These classes hold data only; the rules that change them live in the
service classes (``foodshare/services``). Each class maps to one box in the
class diagram (SRS Section 6).

Every entity has an ``id`` so the repository can store it in a dictionary
and save it to its own JSON file. ``to_dict`` / ``from_dict`` convert an
object to and from plain JSON-friendly data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import Enum


# --------------------------------------------------------------------------
# Enumerations (fixed lists of allowed values)
# --------------------------------------------------------------------------

class Role(str, Enum):
    """A user's role. Roles are values, not subclasses (SRS Table 22)."""
    ADMIN = "Admin"
    STAFF = "Staff"
    VOLUNTEER = "Volunteer"


class BatchStatus(str, Enum):
    AVAILABLE = "available"
    DAMAGED = "damaged"
    EXPIRED = "expired"
    MISSING = "missing"


class HamperStatus(str, Enum):
    SUGGESTED = "suggested"   # generated, waiting for staff to confirm
    CONFIRMED = "confirmed"   # stock reserved, packing task created
    PACKED = "packed"         # volunteer packed it, stock deducted
    COLLECTED = "collected"   # client collected it: counted as distributed
    RETURNED = "returned"     # not collected; items back in stock
    CANCELLED = "cancelled"


class PickupStatus(str, Enum):
    BOOKED = "booked"
    COLLECTED = "collected"
    RETURNED = "returned"
    CANCELLED = "cancelled"


class TaskKind(str, Enum):
    SORT = "Sort donation"
    PACK = "Pack hamper"
    PREPARE = "Prepare pickup"


class TaskStatus(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    COMPLETE = "complete"


class MovementKind(str, Enum):
    """Kinds of stock movement used by the stock movement report (FR-37)."""
    RECEIVED = "received"
    PACKED = "packed"
    DISTRIBUTED = "distributed"
    DISCARDED = "discarded"
    RETURNED = "returned"


# --------------------------------------------------------------------------
# Base class with JSON conversion
# --------------------------------------------------------------------------

class Entity:
    """Mixin that converts a dataclass to and from a plain dictionary."""

    # Nested list fields that hold other dataclasses: {field name: class}
    _nested: dict = {}

    def to_dict(self) -> dict:
        data = asdict(self)
        # Store enum members as their plain value ("Admin", not Role.ADMIN)
        for key, value in data.items():
            if isinstance(value, Enum):
                data[key] = value.value
        for name in self._nested:
            for item in data.get(name, []):
                for key, value in item.items():
                    if isinstance(value, Enum):
                        item[key] = value.value
        return data

    @classmethod
    def from_dict(cls, data: dict):
        allowed = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in allowed}
        for name, nested_cls in cls._nested.items():
            if name in values:
                values[name] = [nested_cls.from_dict(x) for x in values[name]]
        return cls(**values)


# --------------------------------------------------------------------------
# F-06 Access control and privacy
# --------------------------------------------------------------------------

@dataclass
class User(Entity):
    """A staff, admin or volunteer account (FR-32, FR-33)."""
    id: str                       # the username, unique
    role: Role
    secret_hash: str              # salted PBKDF2 hash of password or PIN (NFR-08)
    salt: str
    uses_pin: bool = False        # volunteers log in with a PIN (AD-03)
    active: bool = True
    failed_attempts: int = 0
    locked_until: str | None = None   # ISO date-time, or None

    def __post_init__(self):
        self.role = Role(self.role)


@dataclass
class AuditEntry(Entity):
    """One line of the append-only audit log (FR-35, FR-52, NFR-10)."""
    id: str
    timestamp: str
    user: str
    action: str           # e.g. "view", "create", "update", "delete", "denied"
    record: str           # e.g. "client:C-0001"
    outcome: str = "ok"   # "ok" or "denied"
    detail: str = ""


# --------------------------------------------------------------------------
# F-01 Donations and F-02 Stock
# --------------------------------------------------------------------------

@dataclass
class Item(Entity):
    """A kind of food in the catalogue, with its safety and diet tags.

    ``allergens`` lists what the item contains (e.g. ["peanut"]).
    ``dietary_tags`` lists the needs it satisfies (e.g. ["halal", "vegan"]).
    """
    id: str                       # the item name, e.g. "Rice"
    category: str                 # e.g. "Grains"
    unit: str                     # e.g. "kg", "tin", "pack"
    allergens: list[str] = field(default_factory=list)
    dietary_tags: list[str] = field(default_factory=list)
    essential: bool = False       # essential items get low-stock alerts (FR-11)
    min_level: float = 0.0


@dataclass
class StockBatch(Entity):
    """A quantity of one item with one expiry date and storage type (FR-07)."""
    id: str                       # e.g. "B-0001"
    item: str                     # Item id
    category: str
    quantity: float               # on hand at the food bank
    unit: str
    expiry: str                   # ISO date
    storage: str                  # ambient, chilled or frozen
    received_on: str
    donation_id: str | None = None
    reserved: float = 0.0         # held for confirmed hampers, not yet packed
    status: BatchStatus = BatchStatus.AVAILABLE
    flag_reason: str = ""
    flagged_by: str = ""
    flagged_at: str = ""

    def __post_init__(self):
        self.status = BatchStatus(self.status)

    @property
    def available(self) -> float:
        """Quantity that can still be allocated to a new hamper."""
        if self.status != BatchStatus.AVAILABLE:
            return 0.0
        return round(max(self.quantity - self.reserved, 0.0), 3)


@dataclass
class DonationLine(Entity):
    item: str
    quantity: float
    unit: str
    expiry: str
    storage: str
    batch_id: str = ""


@dataclass
class Donation(Entity):
    """A received donation (FR-01). Donor details are optional (FR-06)."""
    id: str                       # reference number shown on the confirmation (FR-05)
    received_on: str
    recorded_by: str
    lines: list[DonationLine] = field(default_factory=list)
    donor_name: str = ""
    donor_contact: str = ""
    _nested = {"lines": DonationLine}


@dataclass
class DonorOffer(Entity):
    """A donor's proposal to give items, recorded by staff (FR-02)."""
    id: str
    recorded_on: str
    recorded_by: str
    donor_name: str
    items: list[dict] = field(default_factory=list)   # {item, quantity, expiry}
    dropoff_time: str = ""
    status: str = "pending"


@dataclass
class StockMovement(Entity):
    """A change in stock, used by the stock movement report (FR-37)."""
    id: str
    date: str
    batch_id: str
    item: str
    category: str
    quantity: float
    kind: MovementKind
    reference: str = ""           # donation, hamper or flag that caused it

    def __post_init__(self):
        self.kind = MovementKind(self.kind)


# --------------------------------------------------------------------------
# F-03 Client and household profiles
# --------------------------------------------------------------------------

@dataclass
class ClientNote(Entity):
    text: str
    author: str
    written_at: str
    restricted: bool = False      # restricted notes: Staff and Admin only (FR-17)


@dataclass
class Visit(Entity):
    date: str
    hamper_id: str


@dataclass
class Client(Entity):
    """A household receiving food support (FR-13 to FR-19).

    Optional sensitive categories (dietary, religious, medical, allergies,
    accessibility) are stored only when ``consents[category]`` is True (FR-15).
    """
    id: str                       # e.g. "C-0001"
    preferred_name: str
    contact_method: str           # SMS, Phone call, Email or In person (FR-16)
    contact_detail: str = ""
    preferred_language: str = "English"
    adults: int = 1
    children: int = 0
    infants: int = 0
    older: int = 0                # older members (allocated as adults)
    pickup_preference: str = ""
    dietary: list[str] = field(default_factory=list)       # e.g. vegetarian
    religious: list[str] = field(default_factory=list)     # e.g. halal
    medical: list[str] = field(default_factory=list)       # e.g. gluten-free
    allergies: list[str] = field(default_factory=list)     # e.g. peanut
    accessibility: list[str] = field(default_factory=list)
    other_needs_note: str = ""
    consents: dict = field(default_factory=dict)           # category -> bool
    consent_recorded_on: str = ""
    notes: list[ClientNote] = field(default_factory=list)
    visits: list[Visit] = field(default_factory=list)
    kiosk_pin_hash: str = ""
    kiosk_salt: str = ""
    created_on: str = ""
    deleted: bool = False         # True after a deletion request (FR-45)
    _nested = {"notes": ClientNote, "visits": Visit}

    @property
    def household_size(self) -> int:
        return self.adults + self.children + self.infants + self.older

    def requirement_tags(self) -> list[str]:
        """Diet tags every hamper item must carry (Table 15 eligibility)."""
        return sorted(set(self.dietary + self.religious + self.medical))


# --------------------------------------------------------------------------
# F-04 Hampers and pickups
# --------------------------------------------------------------------------

@dataclass
class AllocationRule(Entity):
    """Quantity per person per visit for one food category (Table 15)."""
    id: str                       # the category name, e.g. "Grains"
    unit: str
    per_adult: float = 0.0
    per_child: float = 0.0
    per_infant: float = 0.0

    def describe(self) -> str:
        """Plain text shown to staff beside each hamper line (FR-22)."""
        parts = []
        if self.per_adult:
            parts.append(f"{self.per_adult:g} {self.unit} per adult")
        if self.per_child:
            parts.append(f"{self.per_child:g} {self.unit} per child")
        if self.per_infant:
            parts.append(f"{self.per_infant:g} {self.unit} per infant")
        return f"{self.id}: " + ", ".join(parts)


@dataclass
class HamperLine(Entity):
    item: str
    category: str
    batch_id: str
    quantity: float
    unit: str
    rule_text: str = ""
    packed: bool = False
    substituted_from: str = ""    # original item if this line is a substitute
    substitution_note: str = ""   # respectful note (FR-23)


@dataclass
class Hamper(Entity):
    id: str                       # e.g. "H-0001"
    client_id: str
    pickup_id: str
    created_by: str
    created_at: str
    status: HamperStatus = HamperStatus.SUGGESTED
    lines: list[HamperLine] = field(default_factory=list)
    shortfalls: list[dict] = field(default_factory=list)   # {category, quantity, unit, need}
    adjustments: list[dict] = field(default_factory=list)  # {line, from, to, reason, by, at}
    task_id: str = ""
    _nested = {"lines": HamperLine}

    def __post_init__(self):
        self.status = HamperStatus(self.status)


@dataclass
class UnmetRequest(Entity):
    """A need that could be neither supplied nor safely substituted (FR-46)."""
    id: str
    client_id: str
    hamper_id: str
    category: str
    need: str                     # need category, e.g. "infant needs", "halal"
    quantity: float
    unit: str
    date: str


@dataclass
class Pickup(Entity):
    """A booked collection time with a non-identifying code (FR-24)."""
    id: str
    client_id: str
    date: str
    slot: str                     # e.g. "16:00-18:00"
    code: str                     # e.g. "K27"
    booked_at: str
    status: PickupStatus = PickupStatus.BOOKED
    hamper_id: str = ""

    def __post_init__(self):
        self.status = PickupStatus(self.status)


# --------------------------------------------------------------------------
# F-05 Volunteer tasks
# --------------------------------------------------------------------------

@dataclass
class TaskFlag(Entity):
    issue_type: str               # damaged item, missing stock, incomplete request
    note: str
    raised_by: str
    raised_at: str
    resolved: bool = False


@dataclass
class Task(Entity):
    id: str                       # e.g. "T-0001"
    kind: TaskKind
    title: str
    created_by: str
    created_at: str
    priority: int = 2             # 1 = high, 2 = normal, 3 = low
    assigned_to: str = ""         # empty = open task
    status: TaskStatus = TaskStatus.OPEN
    hamper_id: str = ""
    completed_by: str = ""
    completed_at: str = ""
    flags: list[TaskFlag] = field(default_factory=list)
    _nested = {"flags": TaskFlag}

    def __post_init__(self):
        self.kind = TaskKind(self.kind)
        self.status = TaskStatus(self.status)
