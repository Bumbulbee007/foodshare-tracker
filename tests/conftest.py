"""Shared PyTest fixtures.

Every test gets a brand-new system in a temporary folder, a frozen clock
(30 Oct 2026, 9:00 am) and a seeded random generator, so results never
depend on the real date or on other tests.
"""

import copy
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from foodshare.clock import FixedClock                       # noqa: E402
from foodshare.models import Role, User                      # noqa: E402
from foodshare.security import hash_secret                   # noqa: E402
from foodshare.seed import ITEMS, RULES                      # noqa: E402
from foodshare.system import FoodShareSystem                 # noqa: E402

START = datetime(2026, 10, 30, 9, 0, 0)


@pytest.fixture
def clock():
    return FixedClock(START)


def make_system(data_dir, clock) -> FoodShareSystem:
    system = FoodShareSystem(data_dir, clock=clock, rng=random.Random(42))
    system.repo.replace_all("rules", copy.deepcopy(RULES))
    system.repo.replace_all("items", copy.deepcopy(ITEMS))
    for username, role, secret in [("admin", Role.ADMIN, "Admin#2026!"),
                                   ("grace", Role.STAFF, "Grace#2026!"),
                                   ("margaret", Role.VOLUNTEER, "1234"),
                                   ("david", Role.VOLUNTEER, "5678")]:
        h, s = hash_secret(secret)
        system.repo.add("users", User(id=username, role=role, secret_hash=h, salt=s,
                                      uses_pin=role == Role.VOLUNTEER))
    return system


@pytest.fixture
def system(tmp_path, clock):
    return make_system(tmp_path / "data", clock)


@pytest.fixture
def admin(system):
    return system.repo.get("users", "admin")


@pytest.fixture
def staff(system):
    return system.repo.get("users", "grace")


@pytest.fixture
def volunteer(system):
    return system.repo.get("users", "margaret")


@pytest.fixture
def volunteer2(system):
    return system.repo.get("users", "david")


# ----------------------------------------------------------------- helpers
def add_stock(system, staff, item, quantity, days=30, storage="ambient"):
    """Record a one-line donation and return the new batch."""
    expiry = (system.clock.today() + timedelta(days=days)).isoformat()
    donation = system.stock.record_donation(staff, [
        {"item": item, "quantity": quantity, "expiry": expiry, "storage": storage}])
    return system.repo.get("batches", donation.lines[0].batch_id)


def add_client(system, staff, **overrides):
    data = {"preferred_name": "Test Household", "contact_method": "SMS",
            "contact_detail": "0400 000 000", "adults": 1, "children": 0, "infants": 0}
    data.update(overrides)
    return system.clients.create_client(staff, data)


def book(system, staff, client, days=0, slot="16:00-18:00"):
    return system.hampers.book_pickup(staff, client.id, system.clock.today() + timedelta(days=days), slot)


def full_stock(system, staff):
    """Enough stock in every category for normal hamper tests."""
    for item, qty in [("Rice", 40), ("Canned tomatoes", 30), ("Lentils", 20), ("Chickpeas", 20),
                      ("Tuna", 20), ("Halal chicken", 20), ("Honey", 10), ("Peanut butter", 10),
                      ("Rice puffs", 20), ("UHT milk", 20), ("Soy milk", 20), ("Apples", 20),
                      ("Infant formula", 20), ("Nappies", 10)]:
        add_stock(system, staff, item, qty, days=60)


def packed_hamper(system, staff, volunteer, client, days=0):
    """Book, generate, confirm, assign and pack a hamper. Returns (pickup, hamper)."""
    pickup = book(system, staff, client, days=days)
    hamper = system.hampers.generate_hamper(staff, pickup.code)
    task = system.hampers.confirm_hamper(staff, hamper.id)
    system.tasks.assign_task(staff, task.id, volunteer.id)
    for i in range(len(system.repo.get("hampers", hamper.id).lines)):
        system.tasks.set_line_packed(volunteer, task.id, i)
    system.tasks.complete_task(volunteer, task.id)
    return pickup, system.repo.get("hampers", hamper.id)
