"""F-06: log-in, lock-out, session time-out and user accounts.

Requirements: FR-32, FR-33, NFR-08, NFR-09. Use cases: UC-13, UC-16.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..clock import Clock
from ..errors import AuthenticationError, SessionExpiredError, ValidationError
from ..models import Role, User
from ..repository import JsonRepository
from ..security import AccessControl, AuditService, hash_secret, verify_secret
from ..text import t

MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 5


@dataclass
class Session:
    """A logged-in user plus the time of their last action."""
    user: User
    last_activity: datetime


class AuthService:
    def __init__(self, repo: JsonRepository, clock: Clock, audit: AuditService):
        self.repo = repo
        self.clock = clock
        self.audit = audit

    def login(self, username: str, secret: str) -> Session:
        """Check a username and password/PIN (FR-32).

        The same message is used for every failure, so an attacker cannot
        tell whether a username exists or is locked (AC-28).
        """
        user = self.repo.get("users", (username or "").strip().lower())
        now = self.clock.now()

        if user is None or not user.active:
            self.audit.record(username or "?", "login", "session", "denied", "unknown or inactive")
            raise AuthenticationError(t("login_failed"))

        if user.locked_until and now < datetime.fromisoformat(user.locked_until):
            self.audit.record(user.id, "login", "session", "denied", "account locked")
            raise AuthenticationError(t("login_failed"))

        if not verify_secret(secret or "", user.secret_hash, user.salt):
            user.failed_attempts += 1
            if user.failed_attempts >= MAX_FAILED_ATTEMPTS:          # NFR-08
                user.locked_until = (now + timedelta(minutes=LOCK_MINUTES)).isoformat()
                user.failed_attempts = 0
            self.repo.add("users", user)
            self.audit.record(user.id, "login", "session", "denied", "wrong secret")
            raise AuthenticationError(t("login_failed"))

        user.failed_attempts = 0
        user.locked_until = None
        self.repo.add("users", user)
        self.audit.record(user.id, "login", "session")
        return Session(user=user, last_activity=now)

    def is_expired(self, session: Session) -> bool:
        """True if the session has been idle too long (checked by a timer, no update)."""
        limit = timedelta(minutes=self.repo.settings["session_timeout_minutes"])
        return self.clock.now() - session.last_activity > limit

    def touch(self, session: Session) -> None:
        """Call on every user action; ends the session after inactivity (NFR-09)."""
        limit = timedelta(minutes=self.repo.settings["session_timeout_minutes"])
        now = self.clock.now()
        if now - session.last_activity > limit:
            self.audit.record(session.user.id, "logout", "session", detail="timed out")
            raise SessionExpiredError(t("session_expired"))
        session.last_activity = now


class UserService:
    """Admin-only management of accounts and roles (FR-33, UC-13)."""

    def __init__(self, repo: JsonRepository, access: AccessControl, audit: AuditService):
        self.repo = repo
        self.access = access
        self.audit = audit

    def create_user(self, admin: User, username: str, role: Role | str, secret: str) -> User:
        self.access.require(admin, "users.manage", "users")
        username = (username or "").strip().lower()
        role = Role(role)
        if not username.isidentifier():
            raise ValidationError("Username must be letters, numbers or _ with no spaces.", "username")
        if self.repo.get("users", username):
            raise ValidationError("That username is already taken.", "username")
        uses_pin = role == Role.VOLUNTEER
        if uses_pin:
            if not (secret.isdigit() and len(secret) == 4):
                raise ValidationError("A volunteer PIN must be exactly 4 digits.", "secret")
        elif len(secret) < 8:
            raise ValidationError("A password must be at least 8 characters.", "secret")
        hashed, salt = hash_secret(secret)
        user = User(id=username, role=role, secret_hash=hashed, salt=salt, uses_pin=uses_pin)
        self.repo.add("users", user)
        self.audit.record(admin.id, "create", f"user:{username}", detail=role.value)
        return user

    def set_active(self, admin: User, username: str, active: bool) -> User:
        self.access.require(admin, "users.manage", f"user:{username}")
        user = self.repo.get("users", username)
        if user is None:
            raise ValidationError("No such user.", "username")
        if user.id == admin.id and not active:
            raise ValidationError("You cannot deactivate your own account.", "username")
        user.active = active
        self.repo.add("users", user)
        self.audit.record(admin.id, "update", f"user:{username}",
                          detail="activated" if active else "deactivated")
        return user

    def list_users(self, admin: User) -> list[User]:
        self.access.require(admin, "users.manage", "users")
        return sorted(self.repo.all("users"), key=lambda u: u.id)
