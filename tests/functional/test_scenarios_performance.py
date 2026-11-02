"""End-to-end scenario tests (SRS Section 3.3.3) and performance tests.

The scenarios follow the personas through a whole day, exactly as the
demo does. Performance tests use generated data at the NFR-15 volumes.
"""

import random
import time
from datetime import timedelta

import pytest

from conftest import add_client, book, full_stock
from foodshare.models import Client, HamperStatus, Pickup, StockBatch


def login(system, username):
    secrets = {"admin": "Admin#2026!", "grace": "Grace#2026!", "margaret": "1234"}
    return system.auth.login(username, secrets[username]).user


def test_e2e_scenario_a_receiving_a_donation(system):
    """Scenario A (UC-01, UC-02): a supermarket delivery at 9 am."""
    grace = login(system, "grace")
    today = system.clock.today()
    lines = [{"item": "Rice", "quantity": 40, "expiry": (today + timedelta(days=200)).isoformat(), "storage": "ambient"},
             {"item": "UHT milk", "quantity": 12, "expiry": (today + timedelta(days=5)).isoformat(), "storage": "ambient"},
             {"item": "Apples", "quantity": 6, "expiry": (today + timedelta(days=3)).isoformat(), "storage": "chilled"}]
    # One yoghurt-style item that has already expired is refused on its own
    with pytest.raises(Exception):
        system.stock.record_donation(grace, [{"item": "UHT milk", "quantity": 1,
                                              "expiry": (today - timedelta(days=1)).isoformat(),
                                              "storage": "chilled"}])
    donation = system.stock.record_donation(grace, lines, donor_name="Hillside Supermarket")
    assert "Reference" in system.stock.confirmation_text(donation)
    assert {b.item for b in system.stock.expiring_soon(grace)} == {"UHT milk", "Apples"}


def test_e2e_scenario_b_packing_and_pickup_day(system):
    """Scenario B (UC-04 -> UC-06 -> UC-05 -> UC-07/07a -> UC-09 -> UC-15)."""
    grace, margaret = login(system, "grace"), login(system, "margaret")
    full_stock(system, grace)
    # Grace records Amina's household with consent (UC-04, UC-04a)
    amina = system.clients.create_client(grace, {
        "preferred_name": "Amina Yusuf", "contact_method": "SMS", "contact_detail": "0400 111 222",
        "adults": 1, "children": 2, "infants": 1, "religious": ["halal"], "allergies": ["tree-nut"],
        "consents": {"religious": True, "allergies": True}})
    pickup = system.hampers.book_pickup(grace, amina.id, system.clock.today(), "16:00-18:00")   # UC-06
    message = system.hampers.pickup_message(grace, pickup.id)
    assert pickup.code in message["text"] and "Amina" not in message["text"]

    hamper = system.hampers.generate_hamper(grace, pickup.code)                                  # UC-05
    task = system.hampers.confirm_hamper(grace, hamper.id)
    system.tasks.assign_task(grace, task.id, "margaret")

    view = system.tasks.task_view(margaret, task.id)                                              # UC-07
    assert set(view["tags"]) == {"halal", "tree-nut-free", "infant"}
    legumes = next(l for l in view["lines"] if l["item"] in ("Chickpeas", "Lentils"))
    options = system.tasks.substitutes_for(margaret, task.id, legumes["index"])                  # UC-07a
    system.tasks.substitute_line(margaret, task.id, legumes["index"], options[0]["batch_id"],
                                 "Lentils replaced chickpeas this week.")
    for line in system.tasks.task_view(margaret, task.id)["lines"]:
        system.tasks.set_line_packed(margaret, task.id, line["index"])
    system.tasks.complete_task(margaret, task.id)

    at_counter = system.hampers.counter_lookup(grace, pickup.code)                                # UC-09
    assert at_counter["hamper_status"] == "packed" and "Amina" not in str(at_counter)
    system.hampers.confirm_collection(grace, pickup.code)
    assert system.repo.get("hampers", hamper.id).status == HamperStatus.COLLECTED

    today = system.clock.today()
    demand = system.reports.demand(grace, today, today)                                           # UC-15
    assert demand["summary"]["households_served"] == 1


# ------------------------------------------------------------ performance
@pytest.fixture
def big_system(system):
    """5,000 stock batches and 2,000 households with pickups (NFR-15 volumes)."""
    rnd = random.Random(1)
    repo = system.repo
    items = repo.all("items")
    today = system.clock.today()
    for n in range(1, 5001):
        item = rnd.choice(items)
        repo._store["batches"][f"B-{n:04d}"] = StockBatch(
            id=f"B-{n:04d}", item=item.id, category=item.category, quantity=rnd.randint(1, 30),
            unit=item.unit, expiry=(today + timedelta(days=rnd.randint(1, 400))).isoformat(),
            storage="ambient", received_on=today.isoformat())
    for n in range(1, 2001):
        repo._store["clients"][f"C-{n:04d}"] = Client(
            id=f"C-{n:04d}", preferred_name=f"Household {n}", contact_method="In person",
            adults=rnd.randint(1, 3), children=rnd.randint(0, 3), created_on=today.isoformat())
        repo._store["pickups"][f"P-{n:04d}"] = Pickup(
            id=f"P-{n:04d}", client_id=f"C-{n:04d}", date=today.isoformat(), slot="16:00-18:00",
            code=f"X{n:04d}", booked_at=f"{today.isoformat()}T08:00:{n % 60:02d}")
    for name in ("batches", "clients", "pickups"):
        repo.save(name)
    return system


def timed(fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    return result, time.perf_counter() - start


def test_perf_nfr15_lookups_under_2_seconds(big_system):
    grace = login(big_system, "grace")
    _, t_stock = timed(big_system.stock.stock_summary, grace)
    _, t_client = timed(big_system.clients.list_clients, grace, "Household 1999")
    _, t_code = timed(big_system.hampers.counter_lookup, grace, "X1999")
    assert max(t_stock, t_client, t_code) < 2.0, (t_stock, t_client, t_code)


def test_perf_nfr16_hamper_under_3_seconds(big_system):
    grace = login(big_system, "grace")
    _, seconds = timed(big_system.hampers.generate_hamper, grace, "X0001")
    assert seconds < 3.0, seconds
