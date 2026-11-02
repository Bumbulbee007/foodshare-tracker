"""Time source for the system.

Services never call ``datetime.now()`` directly. They ask a Clock instead, so
tests can freeze or move time (for example to test a 5-minute account lock
or a 24-month retention review) without waiting in real time.
"""

from datetime import date, datetime, timedelta


class Clock:
    """The real clock, used when the application runs normally."""

    def now(self) -> datetime:
        return datetime.now().replace(microsecond=0)

    def today(self) -> date:
        return self.now().date()


class FixedClock(Clock):
    """A clock that stays at a set time until a test moves it."""

    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        """Move time forward, e.g. ``clock.advance(minutes=5)``."""
        self._now = self._now + timedelta(**kwargs)

    def set(self, moment: datetime) -> None:
        self._now = moment
