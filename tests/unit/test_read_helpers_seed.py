"""Unit tests: read-only helpers used by the screens, and the demo data."""

import pytest

from conftest import add_client, add_stock, book, full_stock, packed_hamper
from foodshare.errors import NotFoundError, PermissionDeniedError
from foodshare.models import HamperStatus, Role
from foodshare.seed import reset


def test_pickup_slots_and_settings_copy(system):
    assert system.hampers.pickup_slots() == ["10:00-12:00", "16:00-18:00"]
    settings = system.settings()
    settings["expiry_threshold_days"] = 99           # editing the copy changes nothing
    assert system.settings()["expiry_threshold_days"] == 7


def test_client_pickups_newest_first(system, staff):
    c = add_client(system, staff)
    book(system, staff, c)
    book(system, staff, c, days=7)
    assert [p.date for p in system.hampers.client_pickups(staff, c.id)] == sorted(
        [p.date for p in system.hampers.client_pickups(staff, c.id)], reverse=True)


def test_list_pickups_for_one_day(system, staff):
    c = add_client(system, staff)
    book(system, staff, c)
    book(system, staff, c, days=1)
    assert len(system.hampers.list_pickups(staff, system.clock.today())) == 1
    assert len(system.hampers.list_pickups(staff)) == 2


def test_hamper_for_code_and_list_by_status(system, staff, volunteer):
    full_stock(system, staff)
    p = book(system, staff, add_client(system, staff))
    assert system.hampers.hamper_for_code(staff, p.code) is None
    h = system.hampers.generate_hamper(staff, p.code)
    assert system.hampers.hamper_for_code(staff, p.code).id == h.id
    assert system.hampers.pickup_code_for(h) == p.code
    assert [x.id for x in system.hampers.list_hampers(staff, ["suggested"])] == [h.id]
    assert system.hampers.list_hampers(staff, ["packed"]) == []
    assert system.hampers.get_hamper(staff, h.id).status == HamperStatus.SUGGESTED
    system.hampers.cancel_hamper(staff, h.id)
    assert system.hampers.hamper_for_code(staff, p.code) is None      # cancelled is ignored
    with pytest.raises(NotFoundError):
        system.hampers.get_hamper(staff, "H-9999")


def test_list_volunteers_only_active_volunteers(system, admin, staff):
    system.users.set_active(admin, "david", False)
    assert system.tasks.list_volunteers(staff) == ["margaret"]


def test_all_tasks_and_list_users(system, admin, staff):
    t = system.tasks.create_task(staff, "Sort donation", "x")
    assert [x.id for x in system.tasks.all_tasks(staff)] == [t.id]
    assert {u.id for u in system.users.list_users(admin)} == {"admin", "grace", "margaret", "david"}


def test_session_expiry_check_does_not_extend_session(system, clock):
    session = system.auth.login("grace", "Grace#2026!")
    clock.advance(minutes=9)
    assert not system.auth.is_expired(session)
    clock.advance(minutes=2)
    assert system.auth.is_expired(session)


def test_task_without_hamper_cannot_be_packed(system, staff, volunteer):
    from foodshare.errors import ValidationError
    t = system.tasks.create_task(staff, "Sort donation", "x", "margaret")
    with pytest.raises(ValidationError):
        system.tasks.set_line_packed(volunteer, t.id, 0)


def test_demo_seed_is_ready_for_the_demonstration(tmp_path):
    system = reset(tmp_path / "demo")
    repo = system.repo
    assert {u.id: u.role for u in repo.all("users")} == {
        "admin": Role.ADMIN, "grace": Role.STAFF, "margaret": Role.VOLUNTEER, "david": Role.VOLUNTEER}
    assert len(repo.all("clients")) == 4 and len(repo.all("pickups")) == 3
    hamper = repo.all("hampers")[0]
    assert hamper.status == HamperStatus.CONFIRMED
    grace = repo.get("users", "grace")
    assert system.stock.expiring_soon(grace)                         # something to show on the dashboard
    margaret = repo.get("users", "margaret")
    assert system.tasks.my_tasks(margaret)[0].hamper_id == hamper.id
    # reset again replaces everything
    again = reset(tmp_path / "demo")
    assert len(again.repo.all("clients")) == 4
