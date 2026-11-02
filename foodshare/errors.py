"""Exceptions used across FoodShare Tracker.

Every error the user can cause is raised as one of these classes, so the
presentation layer can catch them and show a friendly message instead of
crashing (NFR-12 fault tolerance).
"""


class FoodShareError(Exception):
    """Base class for all expected (handled) errors in the system."""


class ValidationError(FoodShareError):
    """Raised when user input breaks a business rule (e.g. a past expiry date).

    ``field`` names the input that is wrong so the screen can highlight it
    (AC-13: "the error names the incorrect field").
    """

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


class NotFoundError(FoodShareError):
    """Raised when a record (client, batch, pickup code...) does not exist."""


class PermissionDeniedError(FoodShareError):
    """Raised when a user's role does not allow an action (FR-34, FR-52)."""


class AuthenticationError(FoodShareError):
    """Raised when a log-in or kiosk PIN check fails (FR-32, FR-41)."""


class SessionExpiredError(FoodShareError):
    """Raised when a session has been idle for too long (NFR-09, FR-43)."""
