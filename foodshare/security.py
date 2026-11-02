"""Access control and audit: the single privacy enforcement point (AD-02).

Every service method starts with ``access.require(user, permission)``.
That one call
1. checks the user's role against the role permission matrix (FR-34), and
2. writes an audit entry and raises PermissionDeniedError if it is refused (FR-52).

Passwords and PINs are stored only as salted PBKDF2 hashes (NFR-08).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from .clock import Clock
from .errors import PermissionDeniedError
from .models import AuditEntry, Role, User
from .repository import JsonRepository

A, S, V = Role.ADMIN, Role.STAFF, Role.VOLUNTEER

# Role permission matrix, the code version of SRS Table 22.
# Admin holds every Staff permission plus user, rule, audit and backup management.
PERMISSIONS: dict[str, set[Role]] = {
    "donation.read": {A, S, V},
    "donation.write": {A, S},
    "stock.read": {A, S, V},
    "stock.flag": {A, S, V},
    "stock.configure": {A, S},
    "client.read": {A, S},
    "client.write": {A, S},
    "client.rights": {A, S},       # access, correction and deletion requests
    "hamper.write": {A, S},
    "pickup.write": {A, S},
    "rules.read": {A, S},
    "rules.write": {A},
    "task.manage": {A, S},
    "task.work": {A, S, V},        # view own tasks, pack, substitute, flag
    "report.read": {A, S},
    "users.manage": {A},
    "audit.read": {A},
    "system.admin": {A},           # backups and retention review
}

PBKDF2_ITERATIONS = 120_000


def hash_secret(secret: str, salt: str | None = None) -> tuple[str, str]:
    """Return (hash, salt) for a password or PIN."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return digest.hex(), salt


def verify_secret(secret: str, stored_hash: str, salt: str) -> bool:
    candidate, _ = hash_secret(secret, salt)
    return hmac.compare_digest(candidate, stored_hash)   # constant-time compare


class AuditService:
    """Writes and reads the append-only audit log (FR-35, FR-36, NFR-10).

    There is deliberately no method to edit or delete an entry.
    """

    def __init__(self, repo: JsonRepository, clock: Clock):
        self.repo = repo
        self.clock = clock

    def record(self, username: str, action: str, record: str,
               outcome: str = "ok", detail: str = "") -> AuditEntry:
        entry = AuditEntry(
            id=self.repo.next_id("audit"),
            timestamp=self.clock.now().isoformat(),
            user=username, action=action, record=record,
            outcome=outcome, detail=detail,
        )
        self.repo.add("audit", entry)
        return entry


class AccessControl:
    """Checks a user's role before any business operation runs."""

    def __init__(self, audit: AuditService):
        self.audit = audit

    @staticmethod
    def allowed(user: User, permission: str) -> bool:
        return user is not None and user.active and user.role in PERMISSIONS[permission]

    def require(self, user: User, permission: str, record: str = "") -> None:
        if not self.allowed(user, permission):
            username = user.id if user else "anonymous"
            self.audit.record(username, "denied", record or permission,
                              outcome="denied", detail=permission)
            raise PermissionDeniedError(permission)
