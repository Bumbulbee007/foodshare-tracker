"""Unit tests: hashing, role permissions, audit, log-in and accounts (F-06).

Covers: FR-32 to FR-36, FR-52, NFR-06, NFR-08, NFR-09, NFR-10.
"""

import json

import pytest

from foodshare.errors import (
    AuthenticationError, PermissionDeniedError, SessionExpiredError, ValidationError,
)
from foodshare.models import Role
from foodshare.security import PERMISSIONS, hash_secret, verify_secret
from foodshare.services.auth import LOCK_MINUTES, MAX_FAILED_ATTEMPTS


# ------------------------------------------------------------- hashing
def test_hash_verifies_correct_secret():
    h, s = hash_secret("Grace#2026!")
    assert verify_secret("Grace#2026!", h, s)


def test_hash_rejects_wrong_secret():
    h, s = hash_secret("Grace#2026!")
    assert not verify_secret("grace#2026!", h, s)


def test_same_secret_gets_different_salts():
    assert hash_secret("1234")[0] != hash_secret("1234")[0]


def test_password_never_stored_in_plain_text(system):
    raw = (system.repo.data_dir / "users.json").read_text()
    assert "Grace#2026!" not in raw and "1234\"" not in raw


# ------------------------------------------------- permission matrix
VOLUNTEER_ALLOWED = {"donation.read", "stock.read", "stock.flag", "task.work"}
ADMIN_ONLY = {"rules.write", "users.manage", "audit.read", "system.admin"}


@pytest.mark.parametrize("permission", sorted(PERMISSIONS))
def test_admin_has_every_permission(system, admin, permission):
    assert system.access.allowed(admin, permission)


@pytest.mark.parametrize("permission", sorted(PERMISSIONS))
def test_staff_has_all_but_admin_permissions(system, staff, permission):
    assert system.access.allowed(staff, permission) == (permission not in ADMIN_ONLY)


@pytest.mark.parametrize("permission", sorted(PERMISSIONS))
def test_volunteer_has_only_task_and_stock_permissions(system, volunteer, permission):   # NFR-06
    assert system.access.allowed(volunteer, permission) == (permission in VOLUNTEER_ALLOWED)


def test_inactive_user_has_no_permissions(system, staff):
    staff.active = False
    assert not system.access.allowed(staff, "stock.read")


def test_denied_attempt_is_audited(system, volunteer):                 # FR-52
    with pytest.raises(PermissionDeniedError):
        system.access.require(volunteer, "client.read", "client:C-0001")
    last = system.repo.all("audit")[-1]
    assert (last.user, last.outcome, last.record) == ("margaret", "denied", "client:C-0001")


def test_audit_service_has_no_edit_or_delete():                        # NFR-10
    from foodshare.security import AuditService
    public = [m for m in dir(AuditService) if not m.startswith("_")]
    assert public == ["record"]


# -------------------------------------------------------------- log-in
def test_login_with_correct_password(system):
    session = system.auth.login("grace", "Grace#2026!")
    assert session.user.role == Role.STAFF


def test_login_username_is_case_insensitive(system):
    assert system.auth.login("  GRACE ", "Grace#2026!").user.id == "grace"


def test_volunteer_logs_in_with_pin(system):
    assert system.auth.login("margaret", "1234").user.uses_pin


@pytest.mark.parametrize("username, secret", [("grace", "wrong"), ("nobody", "Grace#2026!"),
                                              ("", ""), ("grace", None)])
def test_failed_login_message_is_always_the_same(system, username, secret):   # AC-28
    with pytest.raises(AuthenticationError) as err:
        system.auth.login(username, secret)
    assert "Check your details" in str(err.value)


def test_four_failures_do_not_lock(system):                            # BVA: limit - 1
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(AuthenticationError):
            system.auth.login("grace", "bad")
    assert system.auth.login("grace", "Grace#2026!")


def test_fifth_failure_locks_even_correct_password(system, clock):      # BVA: limit
    for _ in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(AuthenticationError):
            system.auth.login("grace", "bad")
    with pytest.raises(AuthenticationError):
        system.auth.login("grace", "Grace#2026!")
    clock.advance(minutes=LOCK_MINUTES, seconds=-1)                      # just before unlock
    with pytest.raises(AuthenticationError):
        system.auth.login("grace", "Grace#2026!")
    clock.advance(seconds=1)                                             # exactly 5 minutes
    assert system.auth.login("grace", "Grace#2026!")


def test_success_resets_failure_count(system):
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(AuthenticationError):
            system.auth.login("grace", "bad")
    system.auth.login("grace", "Grace#2026!")
    with pytest.raises(AuthenticationError):
        system.auth.login("grace", "bad")
    assert system.repo.get("users", "grace").failed_attempts == 1


def test_session_lasts_exactly_10_minutes(system, clock):              # NFR-09 BVA
    session = system.auth.login("grace", "Grace#2026!")
    clock.advance(minutes=10)
    system.auth.touch(session)                                           # 10:00 still fine
    clock.advance(minutes=10, seconds=1)
    with pytest.raises(SessionExpiredError):
        system.auth.touch(session)


def test_activity_extends_session(system, clock):
    session = system.auth.login("grace", "Grace#2026!")
    for _ in range(3):
        clock.advance(minutes=9)
        system.auth.touch(session)


# ----------------------------------------------------- user accounts
def test_admin_creates_volunteer_with_pin(system, admin):
    user = system.users.create_user(admin, "Sue", "Volunteer", "4321")
    assert user.id == "sue" and user.uses_pin and system.auth.login("sue", "4321")


@pytest.mark.parametrize("pin", ["123", "12345", "12a4", ""])
def test_volunteer_pin_must_be_4_digits(system, admin, pin):          # BVA 3/5 digits, EP letters
    with pytest.raises(ValidationError):
        system.users.create_user(admin, "sue", "Volunteer", pin)


@pytest.mark.parametrize("password, ok", [("1234567", False), ("12345678", True)])
def test_password_minimum_8_characters(system, admin, password, ok):  # BVA 7/8
    if ok:
        assert system.users.create_user(admin, "newstaff", "Staff", password)
    else:
        with pytest.raises(ValidationError):
            system.users.create_user(admin, "newstaff", "Staff", password)


@pytest.mark.parametrize("name", ["grace", "two words", ""])
def test_username_must_be_unique_and_valid(system, admin, name):
    with pytest.raises(ValidationError):
        system.users.create_user(admin, name, "Staff", "Password1!")


def test_staff_cannot_create_accounts(system, staff):
    with pytest.raises(PermissionDeniedError):
        system.users.create_user(staff, "x", "Staff", "Password1!")


def test_deactivated_account_cannot_log_in(system, admin):             # AC-29
    system.users.set_active(admin, "margaret", False)
    with pytest.raises(AuthenticationError):
        system.auth.login("margaret", "1234")
    system.users.set_active(admin, "margaret", True)
    assert system.auth.login("margaret", "1234")


def test_admin_cannot_deactivate_self(system, admin):
    with pytest.raises(ValidationError):
        system.users.set_active(admin, "admin", False)


def test_deactivate_unknown_user(system, admin):
    with pytest.raises(ValidationError):
        system.users.set_active(admin, "ghost", False)


# ------------------------------------------------------------ audit log
def test_audit_filter_by_user_and_dates(system, admin, staff, clock):  # AC-32
    from datetime import timedelta
    system.audit.record("grace", "view", "client:C-0001")
    clock.advance(days=10)
    system.audit.record("grace", "view", "client:C-0002")
    system.audit.record("admin", "view", "client:C-0003")
    today = clock.today()
    rows = system.audit_log(admin, username="grace", start=today - timedelta(days=6), end=today)
    assert [r.record for r in rows] == ["client:C-0002"]


def test_audit_filter_by_record(system, admin):
    system.audit.record("grace", "view", "client:C-0001")
    system.audit.record("grace", "view", "batch:B-0001")
    assert {r.record for r in system.audit_log(admin, record="client")} == {"client:C-0001"}


def test_audit_reversed_dates_rejected(system, admin, clock):
    from datetime import timedelta
    with pytest.raises(ValidationError):
        system.audit_log(admin, start=clock.today(), end=clock.today() - timedelta(days=1))


def test_staff_cannot_open_audit_log(system, staff):
    with pytest.raises(PermissionDeniedError):
        system.audit_log(staff)


def test_backups_admin_only_and_restore(system, admin, staff):
    names = system.list_backups(admin)
    assert names                                   # created when the system started
    system.restore_backup(admin, names[0])
    with pytest.raises(ValidationError):
        system.restore_backup(admin, "not-a-backup")
    with pytest.raises(PermissionDeniedError):
        system.list_backups(staff)
