"""Wires the layers together (composition root).

``FoodShareSystem`` creates the repository, the access-control and audit
component, and every application service, sharing one clock. The
presentation layer (``app.py``) talks only to this object, never to files.
"""

from __future__ import annotations

import logging
import random
from datetime import date
from pathlib import Path

from .clock import Clock
from .errors import ValidationError
from .models import AuditEntry, User
from .repository import JsonRepository
from .security import AccessControl, AuditService
from .services.auth import AuthService, UserService
from .services.clients import ClientService
from .services.hampers import HamperService
from .services.kiosk import KioskService
from .services.reports import ReportService
from .services.stock import StockService
from .services.tasks import TaskService


class FoodShareSystem:
    def __init__(self, data_dir: str | Path, clock: Clock | None = None,
                 rng: random.Random | None = None):
        self.clock = clock or Clock()
        self.repo = JsonRepository(data_dir)
        self.audit = AuditService(self.repo, self.clock)
        self.access = AccessControl(self.audit)
        self.auth = AuthService(self.repo, self.clock, self.audit)
        self.users = UserService(self.repo, self.access, self.audit)
        self.stock = StockService(self.repo, self.clock, self.access, self.audit)
        self.clients = ClientService(self.repo, self.clock, self.access, self.audit)
        self.hampers = HamperService(self.repo, self.clock, self.access, self.audit, rng)
        self.tasks = TaskService(self.repo, self.clock, self.access, self.audit, self.hampers)
        self.reports = ReportService(self.repo, self.clock, self.access, self.audit)
        self.kiosk = KioskService(self.repo, self.clock, self.audit)
        self.daily_backup()

    def daily_backup(self) -> None:
        """Make today's backup if none exists yet (NFR-14). Never stops the app."""
        try:
            self.repo.backup_if_needed(self.clock.today())
        except OSError as exc:
            logging.getLogger(__name__).error("Daily backup failed: %s", exc)

    def settings(self) -> dict:
        """A read-only copy of the current settings, for display."""
        return dict(self.repo.settings)

    # ---------------------------------------------------- admin functions
    def audit_log(self, user: User, username: str = "", record: str = "",
                  start: date | None = None, end: date | None = None) -> list[AuditEntry]:
        """Filter the audit log by user, record and date range (FR-36, AC-32)."""
        self.access.require(user, "audit.read", "audit")
        if start and end and end < start:
            raise ValidationError("The end date is earlier than the start date.", "end")
        rows = []
        for e in self.repo.all("audit"):
            day = e.timestamp[:10]
            if username and e.user != username:
                continue
            if record and record.lower() not in e.record.lower():
                continue
            if start and day < start.isoformat():
                continue
            if end and day > end.isoformat():
                continue
            rows.append(e)
        return sorted(rows, key=lambda e: e.id, reverse=True)

    def list_backups(self, user: User) -> list[str]:
        self.access.require(user, "system.admin", "backups")
        return self.repo.list_backups()

    def restore_backup(self, user: User, name: str) -> None:
        """Restore one daily backup (NFR-14)."""
        self.access.require(user, "system.admin", "backups")
        if name not in self.repo.list_backups():
            raise ValidationError("Choose a backup from the list.", "backup")
        self.repo.restore_backup(name)
        self.audit.record(user.id, "restore", f"backup:{name}")
