"""F-03 Client and household profiles, consent and data rights.

Requirements: FR-13 to FR-19, FR-44, FR-45, NFR-07, NFR-11.
Use cases: UC-04, UC-04a (record consent), UC-12 (data rights).
"""

from __future__ import annotations

from datetime import date, timedelta

from ..clock import Clock
from ..errors import NotFoundError, ValidationError
from ..models import Client, ClientNote, User
from ..repository import JsonRepository
from ..security import AccessControl, AuditService, hash_secret
from ..text import find_banned_terms, t

CONTACT_METHODS = ("SMS", "Phone call", "Email", "In person")

# Standard option lists (FR-14). Free text goes in other_needs_note.
OPTIONS = {
    "dietary": ["vegetarian", "vegan"],
    "religious": ["halal", "kosher"],
    "medical": ["gluten-free", "low-sugar"],
    "allergies": ["peanut", "tree-nut", "dairy", "egg", "gluten", "soy", "fish", "shellfish", "sesame"],
    "accessibility": ["large print", "interpreter", "mobility support", "quiet pickup area"],
}
OPTIONAL_CATEGORIES = tuple(OPTIONS)

# Fields a client's own data-rights request can correct (FR-44)
CORRECTABLE = ("preferred_name", "contact_method", "contact_detail", "preferred_language",
               "adults", "children", "infants", "older", "pickup_preference")


class ClientService:
    def __init__(self, repo: JsonRepository, clock: Clock, access: AccessControl, audit: AuditService):
        self.repo = repo
        self.clock = clock
        self.access = access
        self.audit = audit

    # ----------------------------------------------------------- validation
    @staticmethod
    def consent_purpose(category: str) -> str:
        """Plain-language reason shown before an optional field is saved (FR-15)."""
        return t(f"consent_purpose.{category}")

    @staticmethod
    def _validate_household(adults, children, infants, older) -> tuple[int, int, int, int]:
        """Whole, non-negative numbers with at least one member (AC-13)."""
        values = {}
        for name, value in (("adults", adults), ("children", children),
                            ("infants", infants), ("older", older)):
            if isinstance(value, float) and not value.is_integer():
                raise ValidationError(f"'{name}' must be a whole number.", name)
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ValidationError(f"'{name}' must be a whole number.", name)
            if number < 0:
                raise ValidationError(f"'{name}' cannot be negative.", name)
            if number > 20:
                raise ValidationError(f"'{name}' looks too large (maximum 20).", name)
            values[name] = number
        if sum(values.values()) == 0:
            raise ValidationError("A household needs at least one member.", "adults")
        return values["adults"], values["children"], values["infants"], values["older"]

    @staticmethod
    def _validate_needs(category: str, values) -> list[str]:
        values = sorted(set(v.strip().lower() for v in (values or []) if v.strip()))
        unknown = [v for v in values if v not in OPTIONS[category]]
        if unknown:
            raise ValidationError(f"Unknown {category} option(s): {', '.join(unknown)}.", category)
        return values

    def _apply_profile(self, client: Client, data: dict) -> None:
        """Validate and copy profile fields from ``data`` onto ``client``."""
        name = str(data.get("preferred_name", client.preferred_name)).strip()
        if not name:
            raise ValidationError("Enter the name the client would like us to use.", "preferred_name")
        if find_banned_terms(name):
            raise ValidationError("Please use the client's preferred name only.", "preferred_name")
        method = data.get("contact_method", client.contact_method)
        if method not in CONTACT_METHODS:
            raise ValidationError("Choose SMS, Phone call, Email or In person.", "contact_method")
        detail = str(data.get("contact_detail", client.contact_detail)).strip()
        if method in ("SMS", "Phone call") and not detail.replace(" ", "").lstrip("+").isdigit():
            raise ValidationError("Enter a phone number using digits only.", "contact_detail")
        if method == "Email" and "@" not in detail:
            raise ValidationError("Enter a valid email address.", "contact_detail")

        client.adults, client.children, client.infants, client.older = self._validate_household(
            data.get("adults", client.adults), data.get("children", client.children),
            data.get("infants", client.infants), data.get("older", client.older))
        client.preferred_name = name
        client.contact_method = method
        client.contact_detail = detail if method != "In person" else ""
        client.preferred_language = str(data.get("preferred_language", client.preferred_language)).strip() or "English"
        client.pickup_preference = str(data.get("pickup_preference", client.pickup_preference)).strip()

        # Optional sensitive categories: saved only with consent (FR-15, AC-12)
        consents = data.get("consents", client.consents) or {}
        for category in OPTIONAL_CATEGORIES:
            if category in data or category in consents:
                values = self._validate_needs(category, data.get(category, getattr(client, category)))
                given = bool(consents.get(category, False))
                client.consents[category] = given
                setattr(client, category, values if given else [])
        if "other_needs_note" in data:
            client.other_needs_note = str(data["other_needs_note"]).strip()
        client.consent_recorded_on = self.clock.today().isoformat()

        pin = data.get("kiosk_pin")
        if pin:
            if not (str(pin).isdigit() and len(str(pin)) == 4):
                raise ValidationError("The kiosk PIN must be exactly 4 digits.", "kiosk_pin")
            client.kiosk_pin_hash, client.kiosk_salt = hash_secret(str(pin))

    # ---------------------------------------------------------------- CRUD
    def create_client(self, user: User, data: dict) -> Client:
        """Create a household profile (FR-13 to FR-16)."""
        self.access.require(user, "client.write", "clients")
        client = Client(id=self.repo.next_id("clients"), preferred_name="", contact_method="In person",
                        created_on=self.clock.today().isoformat())
        self._apply_profile(client, data)
        self.repo.add("clients", client)
        self.audit.record(user.id, "create", f"client:{client.id}")
        return client

    def update_client(self, user: User, client_id: str, data: dict) -> Client:
        """Update a profile, including corrections asked for by the client (FR-44)."""
        self.access.require(user, "client.write", f"client:{client_id}")
        client = self._get(client_id)
        before = {f: getattr(client, f) for f in CORRECTABLE}
        self._apply_profile(client, data)
        self.repo.add("clients", client)
        changed = [f for f in CORRECTABLE if getattr(client, f) != before[f]]
        self.audit.record(user.id, "update", f"client:{client_id}",
                          detail="changed: " + (", ".join(changed) or "needs/consent"))
        return client

    def get_client(self, user: User, client_id: str) -> Client:
        """Open a full profile. Every view is audited (FR-35, AC-31)."""
        self.access.require(user, "client.read", f"client:{client_id}")
        client = self._get(client_id)
        self.audit.record(user.id, "view", f"client:{client_id}")
        return client

    def list_clients(self, user: User, search: str = "") -> list[dict]:
        """Short list for choosing a client: ID, preferred name, household size."""
        self.access.require(user, "client.read", "clients")
        search = search.strip().lower()
        rows = []
        for c in self.repo.all("clients"):
            if c.deleted:
                continue
            if search and search not in c.preferred_name.lower() and search not in c.id.lower():
                continue
            rows.append({"id": c.id, "preferred_name": c.preferred_name,
                         "household_size": c.household_size})
        self.audit.record(user.id, "list", "clients", detail=f"search='{search}'")
        return sorted(rows, key=lambda r: r["id"])

    def _get(self, client_id: str) -> Client:
        client = self.repo.get("clients", client_id)
        if client is None or client.deleted:
            raise NotFoundError(f"Client {client_id} not found.")
        return client

    # --------------------------------------------------------------- notes
    def add_note(self, user: User, client_id: str, text: str, restricted: bool = False) -> ClientNote:
        """Add a note; restricted notes are hidden from all but Staff/Admin (FR-17)."""
        self.access.require(user, "client.write", f"client:{client_id}")
        text = (text or "").strip()
        if not text:
            raise ValidationError("The note is empty.", "note")
        if find_banned_terms(text):
            raise ValidationError("Please reword the note using respectful language.", "note")
        client = self._get(client_id)
        note = ClientNote(text=text, author=user.id, written_at=self.clock.now().isoformat(),
                          restricted=restricted)
        client.notes.append(note)
        self.repo.add("clients", client)
        self.audit.record(user.id, "update", f"client:{client_id}",
                          detail="restricted note added" if restricted else "note added")
        return note

    # --------------------------------------------------------- data rights
    def information_summary(self, user: User, client_id: str) -> str:
        """Printable copy of everything held about a client (FR-18, APP 12)."""
        self.access.require(user, "client.rights", f"client:{client_id}")
        c = self._get(client_id)
        self.audit.record(user.id, "view", f"client:{client_id}", detail="information summary")
        lines = [
            f"Information FoodShare Tracker holds for {c.preferred_name} ({c.id})",
            f"Printed {self.clock.today().isoformat()}",
            "",
            f"Contact method: {c.contact_method} {c.contact_detail}".rstrip(),
            f"Preferred language: {c.preferred_language}",
            f"Household: {c.adults} adult(s), {c.children} child(ren), {c.infants} infant(s), {c.older} older member(s)",
            f"Pickup preference: {c.pickup_preference or '-'}",
        ]
        for category in OPTIONAL_CATEGORIES:
            consent = c.consents.get(category)
            status = "consent given" if consent else ("consent not given" if consent is False else "not asked")
            values = ", ".join(getattr(c, category)) or "-"
            lines.append(f"{category.capitalize()}: {values} ({status})")
        lines.append(f"Other needs note: {c.other_needs_note or '-'}")
        lines.append(f"Consent recorded on: {c.consent_recorded_on or '-'}")
        lines.append(f"Kiosk PIN set: {'yes' if c.kiosk_pin_hash else 'no'}")
        lines.append(f"Visits: {len(c.visits)}")
        for v in c.visits:
            lines.append(f"  - {v.date}: hamper {v.hamper_id}")
        lines.append(f"Notes: {len(c.notes)}")
        for n in c.notes:
            lines.append(f"  - {n.written_at[:10]}{' (restricted)' if n.restricted else ''}: {n.text}")
        return "\n".join(lines)

    def delete_client(self, user: User, client_id: str) -> Client:
        """Remove personal information, keep only de-identified visits (FR-45, AC-15)."""
        self.access.require(user, "client.rights", f"client:{client_id}")
        c = self._get(client_id)
        visit_count = len(c.visits)
        anonymous = Client(id=c.id, preferred_name="(deleted)", contact_method="In person",
                           adults=0, children=0, infants=0, older=0,
                           visits=c.visits, created_on=c.created_on, deleted=True)
        self.repo.add("clients", anonymous)
        self.audit.record(user.id, "delete", f"client:{client_id}",
                          detail=f"personal information removed; {visit_count} visit(s) kept de-identified")
        return anonymous

    def retention_review(self, user: User) -> list[dict]:
        """Profiles with no visit in the retention period (NFR-11)."""
        self.access.require(user, "system.admin", "clients")
        months = self.repo.settings["retention_months"]
        cutoff = self.clock.today() - timedelta(days=round(months * 30.44))
        due = []
        for c in self.repo.all("clients"):
            if c.deleted:
                continue
            last = max((v.date for v in c.visits), default=c.created_on)
            if last and date.fromisoformat(last) < cutoff:
                due.append({"id": c.id, "last_activity": last})
        return sorted(due, key=lambda r: r["last_activity"])
