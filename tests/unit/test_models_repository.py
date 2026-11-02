"""Unit tests: domain model and JSON repository (data layer).

Covers: FR-07, NFR-12, NFR-13, NFR-14, NFR-18.
"""

import json
from datetime import date, timedelta

import pytest

from foodshare.models import (
    AllocationRule, BatchStatus, Client, ClientNote, Hamper, HamperLine,
    HamperStatus, Role, StockBatch, User,
)
from foodshare.repository import BACKUPS_KEPT, JsonRepository


def batch(**kw):
    data = dict(id="B-0001", item="Rice", category="Grains", quantity=10, unit="kg",
                expiry="2026-11-30", storage="ambient", received_on="2026-10-30")
    data.update(kw)
    return StockBatch(**data)


# --------------------------------------------------------------- models
class TestModels:
    def test_batch_available_subtracts_reserved(self):
        assert batch(quantity=10, reserved=4).available == 6

    def test_batch_available_never_negative(self):          # edge
        assert batch(quantity=2, reserved=5).available == 0

    @pytest.mark.parametrize("status", ["damaged", "expired", "missing"])
    def test_flagged_batch_has_nothing_available(self, status):
        assert batch(status=status).available == 0

    def test_enum_values_are_restored_from_text(self):
        u = User(id="x", role="Volunteer", secret_hash="h", salt="s")
        assert u.role is Role.VOLUNTEER

    def test_invalid_enum_value_is_rejected(self):          # negative
        with pytest.raises(ValueError):
            User(id="x", role="Manager", secret_hash="h", salt="s")

    def test_round_trip_keeps_nested_objects(self):
        h = Hamper(id="H-0001", client_id="C-0001", pickup_id="P-0001", created_by="grace",
                   created_at="2026-10-30T09:00:00", status=HamperStatus.CONFIRMED,
                   lines=[HamperLine("Rice", "Grains", "B-0001", 2, "kg")])
        copy = Hamper.from_dict(json.loads(json.dumps(h.to_dict())))
        assert copy == h
        assert copy.to_dict()["status"] == "confirmed"

    def test_client_requirement_tags_combine_categories(self):
        c = Client(id="C-1", preferred_name="A", contact_method="SMS",
                   dietary=["vegan"], religious=["halal"], medical=["gluten-free"])
        assert c.requirement_tags() == ["gluten-free", "halal", "vegan"]

    def test_household_size(self):
        c = Client(id="C-1", preferred_name="A", contact_method="SMS",
                   adults=2, children=1, infants=1, older=1)
        assert c.household_size == 5

    def test_rule_description_names_each_quantity(self):
        r = AllocationRule("Grains", "kg", per_adult=2, per_child=1)
        assert r.describe() == "Grains: 2 kg per adult, 1 kg per child"


# ----------------------------------------------------------- repository
class TestRepository:
    def test_saved_data_survives_reload(self, tmp_path):
        repo = JsonRepository(tmp_path)
        repo.add("batches", batch())
        again = JsonRepository(tmp_path)
        assert again.get("batches", "B-0001") == batch()

    def test_next_id_is_sequential_and_never_reused(self, tmp_path):
        repo = JsonRepository(tmp_path)
        assert repo.next_id("batches") == "B-0001"
        repo.add("batches", batch(id="B-0001"))
        repo.add("batches", batch(id="B-0002"))
        repo.remove("batches", "B-0001")
        assert repo.next_id("batches") == "B-0003"

    def test_find_matches_every_criterion(self, tmp_path):
        repo = JsonRepository(tmp_path)
        repo.add("batches", batch(id="B-0001", item="Rice"))
        repo.add("batches", batch(id="B-0002", item="Pasta"))
        assert [b.id for b in repo.find("batches", item="Pasta")] == ["B-0002"]

    def test_get_missing_returns_none(self, tmp_path):
        assert JsonRepository(tmp_path).get("clients", "C-9999") is None

    def test_corrupted_file_does_not_crash_start_up(self, tmp_path):   # NFR-12
        (tmp_path / "batches.json").write_text("{not json")
        repo = JsonRepository(tmp_path)
        assert repo.all("batches") == []

    def test_bad_record_is_skipped_but_good_ones_load(self, tmp_path):
        good = batch().to_dict()
        (tmp_path / "batches.json").write_text(json.dumps([good, {"id": "B-0002"}]))
        assert [b.id for b in JsonRepository(tmp_path).all("batches")] == ["B-0001"]

    def test_failed_save_leaves_original_file_intact(self, tmp_path, monkeypatch):   # NFR-13
        repo = JsonRepository(tmp_path)
        repo.add("batches", batch())
        before = (tmp_path / "batches.json").read_text()

        def crash(*args, **kwargs):
            raise OSError("power cut")
        monkeypatch.setattr(json, "dump", crash)
        repo._store["batches"]["B-0002"] = batch(id="B-0002")
        with pytest.raises(OSError):
            repo.save("batches")
        assert (tmp_path / "batches.json").read_text() == before
        assert not list(tmp_path.glob("*.tmp"))          # temp file cleaned up

    def test_settings_have_defaults(self, tmp_path):
        assert JsonRepository(tmp_path).settings["expiry_threshold_days"] == 7

    def test_one_backup_per_day(self, tmp_path):                      # NFR-14
        repo = JsonRepository(tmp_path)
        repo.add("batches", batch())
        assert repo.backup_if_needed(date(2026, 10, 30)) is not None
        assert repo.backup_if_needed(date(2026, 10, 30)) is None      # second call same day
        assert (tmp_path / "backups" / "2026-10-30" / "batches.json").exists()

    def test_only_newest_14_backups_are_kept(self, tmp_path):         # BVA: 15th removes oldest
        repo = JsonRepository(tmp_path)
        repo.add("batches", batch())
        first = date(2026, 10, 1)
        for n in range(BACKUPS_KEPT + 1):
            repo.backup_if_needed(first + timedelta(days=n))
        names = repo.list_backups()
        assert len(names) == BACKUPS_KEPT
        assert "2026-10-01" not in names and "2026-10-15" in names

    def test_restore_brings_back_old_data(self, tmp_path):
        repo = JsonRepository(tmp_path)
        repo.add("batches", batch())
        repo.backup_if_needed(date(2026, 10, 30))
        repo.remove("batches", "B-0001")
        repo.restore_backup("2026-10-30")
        assert repo.get("batches", "B-0001") is not None

    def test_restore_unknown_backup_fails(self, tmp_path):           # negative
        with pytest.raises(FileNotFoundError):
            JsonRepository(tmp_path).restore_backup("1999-01-01")

    def test_nested_notes_round_trip(self, tmp_path):
        repo = JsonRepository(tmp_path)
        c = Client(id="C-0001", preferred_name="A", contact_method="SMS",
                   notes=[ClientNote("hello", "grace", "2026-10-30T09:00:00", restricted=True)])
        repo.add("clients", c)
        assert JsonRepository(tmp_path).get("clients", "C-0001").notes[0].restricted is True


def test_presentation_layer_never_touches_files():                   # NFR-18 (code review as a test)
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    sources = [root / "app.py", *sorted((root / "ui").glob("*.py"))]
    assert len(sources) > 1
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for forbidden in ("open(", "json.load", "json.dump", "import repository",
                          "JsonRepository", ".repo."):
            assert forbidden not in text, f"{path.name} must not use {forbidden!r}"


class TestAppendOnly:
    """The audit log and stock movements can only grow (NFR-10)."""

    def entry(self, n):
        from foodshare.models import AuditEntry
        return AuditEntry(id=f"A-{n:04d}", timestamp="2026-10-30T09:00:00", user="grace",
                          action="view", record="client:C-0001")

    def test_entries_are_appended_one_line_each(self, tmp_path):
        repo = JsonRepository(tmp_path)
        repo.add("audit", self.entry(1))
        repo.add("audit", self.entry(2))
        lines = (tmp_path / "audit.jsonl").read_text().splitlines()
        assert len(lines) == 2
        assert [e.id for e in JsonRepository(tmp_path).all("audit")] == ["A-0001", "A-0002"]

    def test_existing_entry_cannot_be_replaced(self, tmp_path):
        repo = JsonRepository(tmp_path)
        repo.add("audit", self.entry(1))
        with pytest.raises(ValueError):
            repo.add("audit", self.entry(1))

    @pytest.mark.parametrize("action", ["save", "remove"])
    def test_append_only_cannot_be_rewritten_or_deleted(self, tmp_path, action):
        repo = JsonRepository(tmp_path)
        repo.add("audit", self.entry(1))
        with pytest.raises(ValueError):
            repo.save("audit") if action == "save" else repo.remove("audit", "A-0001")

    def test_half_written_last_line_is_skipped(self, tmp_path):     # power cut mid-append
        repo = JsonRepository(tmp_path)
        repo.add("audit", self.entry(1))
        with (tmp_path / "audit.jsonl").open("a") as fh:
            fh.write('{"id": "A-0002", "timest')
        assert [e.id for e in JsonRepository(tmp_path).all("audit")] == ["A-0001"]


class TestWindowsFileLocks:
    """Defect D-08: OneDrive or antivirus briefly locking a data file on Windows."""

    def test_save_retries_while_file_is_locked(self, tmp_path, monkeypatch):
        import os as real_os
        repo = JsonRepository(tmp_path)
        calls = {"n": 0}
        original = real_os.replace

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] <= 2:                       # locked for the first two tries
                raise PermissionError("[WinError 5] Access is denied")
            return original(src, dst)
        monkeypatch.setattr("foodshare.repository.os.replace", flaky_replace)
        monkeypatch.setattr("foodshare.repository.time.sleep", lambda s: None)
        repo.add("batches", batch())
        assert calls["n"] == 3
        assert JsonRepository(tmp_path).get("batches", "B-0001") is not None

    def test_still_locked_falls_back_to_copy_and_saves(self, tmp_path, monkeypatch):
        repo = JsonRepository(tmp_path)

        def always_locked(src, dst):
            raise PermissionError("[WinError 5] Access is denied")
        monkeypatch.setattr("foodshare.repository.os.replace", always_locked)
        monkeypatch.setattr("foodshare.repository.time.sleep", lambda s: None)
        repo.add("batches", batch())
        assert JsonRepository(tmp_path).get("batches", "B-0001") is not None
        assert not list(tmp_path.glob("*.tmp"))
