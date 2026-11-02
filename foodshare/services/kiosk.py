"""F-08 Client self-service kiosk.

Requirements: FR-41 to FR-43, NFR-22. Use case: UC-11.

A kiosk session is not a user account: it is opened with a pickup code and
a 4-digit PIN, can only change the pickup slot and contact method, and
ends after 60 seconds without input. It never shows allergies, notes or
visit history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..clock import Clock
from ..errors import AuthenticationError, SessionExpiredError, ValidationError
from ..models import PickupStatus
from ..repository import JsonRepository
from ..security import AuditService, verify_secret
from ..text import t
from .clients import CONTACT_METHODS

MAX_PIN_ATTEMPTS = 3


@dataclass
class KioskSession:
    pickup_id: str
    client_id: str
    last_input: datetime
    pending: dict = field(default_factory=dict)   # unsaved choices


class KioskService:
    def __init__(self, repo: JsonRepository, clock: Clock, audit: AuditService):
        self.repo = repo
        self.clock = clock
        self.audit = audit
        self._failed: dict[str, int] = {}          # pickup code -> wrong PINs

    def start(self, code: str, pin: str) -> KioskSession:
        """Open a session with a pickup code and PIN (FR-41, AC-39)."""
        code = (code or "").strip().upper()
        pickup = next((p for p in self.repo.all("pickups")
                       if p.code == code and p.status == PickupStatus.BOOKED), None)
        client = self.repo.get("clients", pickup.client_id) if pickup else None
        if client is None or not client.kiosk_pin_hash:
            raise AuthenticationError(t("kiosk.ask_staff"))
        if self._failed.get(code, 0) >= MAX_PIN_ATTEMPTS:
            raise AuthenticationError(t("kiosk.ask_staff"))
        if not verify_secret(pin or "", client.kiosk_pin_hash, client.kiosk_salt):
            self._failed[code] = self._failed.get(code, 0) + 1
            self.audit.record("kiosk", "login", f"pickup:{pickup.id}", "denied", "wrong PIN")
            raise AuthenticationError(t("kiosk.ask_staff"))
        self._failed.pop(code, None)
        return KioskSession(pickup_id=pickup.id, client_id=client.id, last_input=self.clock.now())

    def is_expired(self, session: KioskSession) -> bool:
        """Checked by a screen timer; does not count as input."""
        limit = timedelta(seconds=self.repo.settings["kiosk_timeout_seconds"])
        return self.clock.now() - session.last_input > limit

    def touch(self, session: KioskSession) -> None:
        """Reset to the start after 60 seconds without input (FR-43, AC-41)."""
        limit = timedelta(seconds=self.repo.settings["kiosk_timeout_seconds"])
        now = self.clock.now()
        if now - session.last_input > limit:
            session.pending.clear()                # unsaved changes are discarded
            raise SessionExpiredError(t("kiosk.timeout"))
        session.last_input = now

    def current_choices(self, session: KioskSession) -> dict:
        """Only the values the kiosk may show (AC-42): slot and contact method."""
        self.touch(session)
        pickup = self.repo.get("pickups", session.pickup_id)
        client = self.repo.get("clients", session.client_id)
        return {"date": pickup.date, "slot": pickup.slot,
                "slots": list(self.repo.settings["pickup_slots"]),
                "contact_method": client.contact_method,
                "contact_methods": list(CONTACT_METHODS)}

    def choose(self, session: KioskSession, field_name: str, value: str) -> None:
        """Hold one answer until the client presses Save (one question per screen)."""
        self.touch(session)
        if field_name == "slot" and value not in self.repo.settings["pickup_slots"]:
            raise ValidationError("Please choose one of the times shown.", "slot")
        if field_name == "contact_method" and value not in CONTACT_METHODS:
            raise ValidationError("Please choose one of the options shown.", "contact_method")
        if field_name not in ("slot", "contact_method"):
            raise ValidationError("This cannot be changed here.", field_name)
        session.pending[field_name] = value

    def save(self, session: KioskSession) -> str:
        """Save the held answers; staff see the new values (AC-38)."""
        self.touch(session)
        if not session.pending:
            raise ValidationError("Nothing has changed yet.", "pending")
        pickup = self.repo.get("pickups", session.pickup_id)
        client = self.repo.get("clients", session.client_id)

        method = session.pending.get("contact_method")
        if method:
            detail = client.contact_detail
            has_phone = detail.replace(" ", "").lstrip("+").isdigit()
            if (method in ("SMS", "Phone call") and not has_phone) or (method == "Email" and "@" not in detail):
                # The kiosk never asks for a new number or address on a shared screen
                session.pending.pop("contact_method")
                raise ValidationError(t("kiosk.ask_staff"), "contact_method")

        if "slot" in session.pending:
            pickup.slot = session.pending["slot"]
            self.repo.add("pickups", pickup)
        if method:
            client.contact_method = method
            self.repo.add("clients", client)
        self.audit.record("kiosk", "update", f"client:{client.id}",
                          detail="changed: " + ", ".join(sorted(session.pending)))
        session.pending.clear()
        return t("kiosk.saved")
