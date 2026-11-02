"""Data layer: the shared repository (SRS Section 8, AD-04, AD-05).

All data lives in JSON files, one per entity type, inside ``data_dir``.
The repository loads every file into memory when the system starts
(fast look-ups, NFR-15) and is the ONLY code that reads or writes those
files (NFR-18). Services ask the repository for objects; screens never
touch the files.

Reliability measures written once, here:
* atomic saves: write to a temporary file, then replace the original, so a
  crash mid-save never leaves a half-written file (NFR-13);
* a daily backup of every file, keeping the 14 most recent (NFR-14).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from datetime import date
from pathlib import Path

from .models import (
    AllocationRule, AuditEntry, Client, Donation, DonorOffer, Hamper, Item,
    Pickup, StockBatch, StockMovement, Task, UnmetRequest, User,
)

log = logging.getLogger(__name__)

# Collection name -> (entity class, ID prefix used for new records)
COLLECTIONS = {
    "users": (User, ""),
    "items": (Item, ""),
    "batches": (StockBatch, "B"),
    "donations": (Donation, "D"),
    "offers": (DonorOffer, "O"),
    "movements": (StockMovement, "M"),
    "clients": (Client, "C"),
    "rules": (AllocationRule, ""),
    "hampers": (Hamper, "H"),
    "pickups": (Pickup, "P"),
    "unmet": (UnmetRequest, "U"),
    "tasks": (Task, "T"),
    "audit": (AuditEntry, "A"),
}

DEFAULT_SETTINGS = {
    "expiry_threshold_days": 7,                     # FR-09
    "priority_needs": ["Infant formula", "Rice"],   # FR-03
    "pickup_slots": ["10:00-12:00", "16:00-18:00"],
    "session_timeout_minutes": 10,                  # NFR-09
    "kiosk_timeout_seconds": 60,                    # FR-43
    "retention_months": 24,                         # NFR-11
}

BACKUPS_KEPT = 14

# Append-only collections are stored as JSON Lines (one record per line):
# adding a record appends one line instead of rewriting the file, and no
# code path can edit or delete an existing line (NFR-10).
APPEND_ONLY = {"audit", "movements"}


class JsonRepository:
    """Loads, holds and saves every collection of entities."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir = self.data_dir / "backups"
        # In-memory store: collection name -> {id: entity}
        self._store: dict[str, dict] = {}
        self.settings: dict = {}
        self.load_all()

    # ------------------------------------------------------------------ load
    def load_all(self) -> None:
        for name, (cls, _prefix) in COLLECTIONS.items():
            if name in APPEND_ONLY:
                records = self._read_lines(self.data_dir / f"{name}.jsonl")
            else:
                records = self._read_json(self.data_dir / f"{name}.json", default=[])
            self._store[name] = {}
            for raw in records:
                try:
                    entity = cls.from_dict(raw)
                    self._store[name][entity.id] = entity
                except (TypeError, ValueError) as exc:
                    # Skip a damaged record instead of refusing to start (NFR-12)
                    log.error("Skipped bad record in %s: %s", name, exc)
        saved = self._read_json(self.data_dir / "settings.json", default={})
        self.settings = {**DEFAULT_SETTINGS, **saved}

    @staticmethod
    def _read_json(path: Path, default):
        if not path.exists():
            return default
        try:
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Could not read %s: %s", path, exc)
            return default

    @staticmethod
    def _read_lines(path: Path) -> list:
        if not path.exists():
            return []
        records = []
        with path.open(encoding="utf-8") as fh:
            for number, line in enumerate(fh, start=1):
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:   # e.g. half-written last line after a power cut
                        log.error("Skipped damaged line %s in %s", number, path.name)
        return records

    # ------------------------------------------------------------ read/query
    def all(self, collection: str) -> list:
        return list(self._store[collection].values())

    def get(self, collection: str, record_id: str):
        """Return one entity, or None if there is no such id."""
        return self._store[collection].get(record_id)

    def find(self, collection: str, **criteria) -> list:
        """Return entities whose attributes equal every given value."""
        return [
            e for e in self._store[collection].values()
            if all(getattr(e, k) == v for k, v in criteria.items())
        ]

    def next_id(self, collection: str) -> str:
        """Next free ID such as 'B-0007'. IDs are never reused."""
        prefix = COLLECTIONS[collection][1]
        numbers = [
            int(key.split("-")[-1]) for key in self._store[collection]
            if key.startswith(prefix + "-") and key.split("-")[-1].isdigit()
        ]
        return f"{prefix}-{(max(numbers) + 1 if numbers else 1):04d}"

    # ----------------------------------------------------------------- write
    def add(self, collection: str, entity) -> None:
        """Add or replace an entity and save the collection immediately."""
        if collection in APPEND_ONLY:
            if entity.id in self._store[collection]:
                raise ValueError(f"{collection} is append-only; {entity.id} already exists")
            self._store[collection][entity.id] = entity
            with (self.data_dir / f"{collection}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entity.to_dict(), ensure_ascii=False) + "\n")
            return
        self._store[collection][entity.id] = entity
        self.save(collection)

    def replace_all(self, collection: str, entities: list) -> None:
        """Swap a whole collection in one save (used for the rule set)."""
        self._store[collection] = {e.id: e for e in entities}
        self.save(collection)

    def remove(self, collection: str, record_id: str) -> None:
        if collection in APPEND_ONLY:
            raise ValueError(f"{collection} is append-only")
        self._store[collection].pop(record_id, None)
        self.save(collection)

    def save(self, collection: str) -> None:
        if collection in APPEND_ONLY:
            raise ValueError(f"{collection} is append-only and cannot be rewritten")
        records = [e.to_dict() for e in self._store[collection].values()]
        self._atomic_write(self.data_dir / f"{collection}.json", records)

    def save_settings(self) -> None:
        self._atomic_write(self.data_dir / "settings.json", self.settings)

    def _atomic_write(self, path: Path, payload) -> None:
        """Write to a temp file in the same folder, then swap it in (NFR-13)."""
        fd, tmp_name = tempfile.mkstemp(dir=self.data_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            self._replace_with_retry(tmp_name, path)
        except Exception:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
            raise

    @staticmethod
    def _replace_with_retry(source: str, target: Path, attempts: int = 6) -> None:
        """Swap the new file in, coping with Windows file locks (defect D-08).

        Antivirus scanners, cloud sync (OneDrive) and search indexing open a
        file for a moment just after it is written. While they hold it,
        Windows refuses to rename over it ("Access is denied"). We retry a few
        times; if the file is still locked we copy the new contents over the
        old file instead. That copy is not atomic, but these scanners allow
        writing, and the data is saved rather than lost. It is logged.
        """
        for attempt in range(attempts):
            try:
                os.replace(source, target)   # atomic on Windows and macOS
                return
            except PermissionError:
                time.sleep(0.02 * (attempt + 1))
        log.warning("%s stayed locked; saving by copying instead of renaming", target.name)
        shutil.copyfile(source, target)
        try:
            os.remove(source)
        except OSError:
            pass    # a leftover .tmp file is harmless; it is never read

    # --------------------------------------------------------------- backups
    def backup_if_needed(self, today: date) -> Path | None:
        """Make today's backup if it does not exist yet; keep the newest 14."""
        target = self.backup_dir / today.isoformat()
        if target.exists():
            return None
        target.mkdir(parents=True)
        for path in list(self.data_dir.glob("*.json")) + list(self.data_dir.glob("*.jsonl")):
            shutil.copy2(path, target / path.name)
        self._prune_backups()
        return target

    def list_backups(self) -> list[str]:
        if not self.backup_dir.exists():
            return []
        return sorted((p.name for p in self.backup_dir.iterdir() if p.is_dir()), reverse=True)

    def _prune_backups(self) -> None:
        for name in self.list_backups()[BACKUPS_KEPT:]:
            shutil.rmtree(self.backup_dir / name)

    def restore_backup(self, name: str) -> None:
        """Copy a backup's files over the live data and reload (NFR-14)."""
        source = self.backup_dir / name
        if not source.is_dir():
            raise FileNotFoundError(name)
        for path in list(source.glob("*.json")) + list(source.glob("*.jsonl")):
            shutil.copy2(path, self.data_dir / path.name)
        self.load_all()
