"""Demo data so the system can be shown straight away.

Run ``python -m foodshare.seed --reset`` to wipe ``data/`` and start again.
All people below are fictional; the households match the SRS personas.

Demo log-ins (change them before real use):
    admin    / Admin#2026!   (Admin)
    grace    / Grace#2026!   (Staff, persona P1)
    margaret / PIN 1234      (Volunteer, persona P3)
    david    / PIN 5678      (Volunteer)
Kiosk: Amina's pickup code is shown on the Pickups screen; her kiosk PIN is 2468.
"""

from __future__ import annotations

import shutil
import sys
from datetime import timedelta
from pathlib import Path

from .models import AllocationRule, Item, Role, User
from .security import hash_secret
from .system import FoodShareSystem

ALL_TAGS = ["vegetarian", "vegan", "halal", "kosher", "gluten-free", "low-sugar"]

RULES = [  # Table 15 illustrative defaults (AS-05: the manager sets real values)
    AllocationRule("Grains", "kg", per_adult=2, per_child=1),
    AllocationRule("Canned vegetables", "tin", per_adult=2, per_child=1),
    AllocationRule("Legumes", "tin", per_adult=1, per_child=1),
    AllocationRule("Protein", "tin", per_adult=2, per_child=1),
    AllocationRule("Spreads", "jar", per_adult=0.5, per_child=0.5),
    AllocationRule("Breakfast", "pack", per_adult=1, per_child=1),
    AllocationRule("Dairy", "L", per_adult=1, per_child=1),
    AllocationRule("Fresh produce", "kg", per_adult=1, per_child=0.5),
    AllocationRule("Infant formula", "tin", per_infant=2),
    AllocationRule("Nappies", "pack", per_infant=1),
]

ITEMS = [
    Item("Rice", "Grains", "kg", [], ALL_TAGS, essential=True, min_level=20),
    Item("Pasta", "Grains", "kg", ["gluten"], ["vegetarian", "vegan", "halal", "kosher", "low-sugar"]),
    Item("Canned tomatoes", "Canned vegetables", "tin", [], ALL_TAGS),
    Item("Corn kernels", "Canned vegetables", "tin", [], ALL_TAGS),
    Item("Chickpeas", "Legumes", "tin", [], ALL_TAGS),
    Item("Lentils", "Legumes", "tin", [], ALL_TAGS),
    Item("Kidney beans", "Legumes", "tin", [], ALL_TAGS),
    Item("Tuna", "Protein", "tin", ["fish"], ["halal", "kosher", "gluten-free", "low-sugar"]),
    Item("Halal chicken", "Protein", "tin", [], ["halal", "gluten-free", "low-sugar"]),
    Item("Canned beef", "Protein", "tin", [], ["gluten-free", "low-sugar"]),
    Item("Peanut butter", "Spreads", "jar", ["peanut"], ["vegetarian", "vegan", "halal", "kosher", "gluten-free"]),
    Item("Almond spread", "Spreads", "jar", ["tree-nut"], ["vegetarian", "vegan", "halal", "kosher", "gluten-free"]),
    Item("Honey", "Spreads", "jar", [], ["vegetarian", "halal", "kosher", "gluten-free"]),
    Item("Breakfast cereal", "Breakfast", "pack", ["gluten"], ["vegetarian", "halal", "kosher"]),
    Item("Rolled oats", "Breakfast", "pack", ["gluten"], ["vegetarian", "vegan", "halal", "kosher", "low-sugar"]),
    Item("Rice puffs", "Breakfast", "pack", [], ["vegetarian", "vegan", "halal", "kosher", "gluten-free"]),
    Item("UHT milk", "Dairy", "L", ["dairy"], ["vegetarian", "halal", "kosher", "gluten-free", "low-sugar"]),
    Item("Soy milk", "Dairy", "L", ["soy"], ["vegetarian", "vegan", "halal", "kosher", "gluten-free"]),
    Item("Infant formula", "Infant formula", "tin", ["dairy"], ["vegetarian", "halal", "kosher", "gluten-free"],
         essential=True, min_level=10),
    Item("Nappies", "Nappies", "pack", [], ALL_TAGS, essential=True, min_level=5),
    Item("Apples", "Fresh produce", "kg", [], ALL_TAGS),
    Item("Potatoes", "Fresh produce", "kg", [], ALL_TAGS),
]


def seed_demo(data_dir: str | Path) -> FoodShareSystem:
    system = FoodShareSystem(data_dir)
    repo = system.repo
    today = system.clock.today()

    def days(n: int) -> str:
        return (today + timedelta(days=n)).isoformat()

    # Accounts. The first admin is created directly; the rest through the service.
    h, s = hash_secret("Admin#2026!")
    repo.add("users", User(id="admin", role=Role.ADMIN, secret_hash=h, salt=s))
    admin = repo.get("users", "admin")
    system.users.create_user(admin, "grace", Role.STAFF, "Grace#2026!")
    system.users.create_user(admin, "margaret", Role.VOLUNTEER, "1234")
    system.users.create_user(admin, "david", Role.VOLUNTEER, "5678")
    grace = repo.get("users", "grace")
    margaret = repo.get("users", "margaret")

    repo.replace_all("rules", RULES)
    repo.replace_all("items", ITEMS)

    # Stock arrives as donations (some batches expire soon to show FR-09).
    system.stock.record_donation(grace, [
        {"item": "Rice", "quantity": 25, "expiry": days(200), "storage": "ambient"},
        {"item": "Rice", "quantity": 6, "expiry": days(5), "storage": "ambient"},
        {"item": "Pasta", "quantity": 12, "expiry": days(240), "storage": "ambient"},
        {"item": "Canned tomatoes", "quantity": 30, "expiry": days(400), "storage": "ambient"},
        {"item": "Corn kernels", "quantity": 12, "expiry": days(380), "storage": "ambient"},
        {"item": "Lentils", "quantity": 20, "expiry": days(300), "storage": "ambient"},
        {"item": "Kidney beans", "quantity": 8, "expiry": days(320), "storage": "ambient"},
        {"item": "Chickpeas", "quantity": 2, "expiry": days(250), "storage": "ambient"},
    ], donor_name="Hillside Supermarket", donor_contact="manager@hillside.example")
    system.stock.record_donation(grace, [
        {"item": "Tuna", "quantity": 15, "expiry": days(500), "storage": "ambient"},
        {"item": "Halal chicken", "quantity": 10, "expiry": days(420), "storage": "ambient"},
        {"item": "Canned beef", "quantity": 10, "expiry": days(450), "storage": "ambient"},
        {"item": "Peanut butter", "quantity": 6, "expiry": days(180), "storage": "ambient"},
        {"item": "Almond spread", "quantity": 4, "expiry": days(160), "storage": "ambient"},
        {"item": "Honey", "quantity": 5, "expiry": days(700), "storage": "ambient"},
        {"item": "Breakfast cereal", "quantity": 10, "expiry": days(150), "storage": "ambient"},
        {"item": "Rolled oats", "quantity": 8, "expiry": days(200), "storage": "ambient"},
        {"item": "Rice puffs", "quantity": 6, "expiry": days(120), "storage": "ambient"},
        {"item": "UHT milk", "quantity": 20, "expiry": days(90), "storage": "ambient"},
        {"item": "Soy milk", "quantity": 8, "expiry": days(100), "storage": "ambient"},
        {"item": "Infant formula", "quantity": 12, "expiry": days(365), "storage": "ambient"},
        {"item": "Nappies", "quantity": 6, "expiry": days(1000), "storage": "ambient"},
    ])  # anonymous donation (FR-06)
    system.stock.record_donation(grace, [
        {"item": "Apples", "quantity": 10, "expiry": days(6), "storage": "chilled"},
        {"item": "Potatoes", "quantity": 15, "expiry": days(20), "storage": "ambient"},
    ], donor_name="Community garden")

    # Households (fictional; Amina matches persona P2)
    amina = system.clients.create_client(grace, {
        "preferred_name": "Amina Yusuf", "contact_method": "SMS", "contact_detail": "0400 111 222",
        "preferred_language": "Arabic", "adults": 1, "children": 2, "infants": 1,
        "pickup_preference": "After 4 pm",
        "religious": ["halal"], "allergies": ["tree-nut"],
        "consents": {"religious": True, "allergies": True, "dietary": False, "medical": False,
                     "accessibility": False},
        "kiosk_pin": "2468"})
    sam = system.clients.create_client(grace, {
        "preferred_name": "Sam Nguyen", "contact_method": "Phone call", "contact_detail": "0400 333 444",
        "adults": 2, "children": 2, "allergies": ["peanut"],
        "consents": {"allergies": True}})
    lee = system.clients.create_client(grace, {
        "preferred_name": "Mr Lee", "contact_method": "In person", "adults": 0, "older": 1,
        "medical": ["gluten-free"], "accessibility": ["large print"],
        "consents": {"medical": True, "accessibility": True}})
    system.clients.create_client(grace, {
        "preferred_name": "Priya", "contact_method": "Email", "contact_detail": "priya@example.com",
        "adults": 2, "dietary": ["vegetarian"], "consents": {"dietary": True}})
    system.clients.add_note(grace, amina.id, "Prefers a quiet pickup; youngest child is 8 months.",
                            restricted=True)

    # Today's pickups; Sam's hamper is already confirmed with a packing task
    system.hampers.book_pickup(grace, amina.id, today, "16:00-18:00")
    sam_pickup = system.hampers.book_pickup(grace, sam.id, today, "16:00-18:00")
    system.hampers.book_pickup(grace, lee.id, today, "10:00-12:00")
    hamper = system.hampers.generate_hamper(grace, sam_pickup.code)
    task = system.hampers.confirm_hamper(grace, hamper.id, priority=1)
    system.tasks.assign_task(grace, task.id, "margaret")

    # Other volunteer tasks
    system.tasks.create_task(grace, "Sort donation", "Sort the Hillside Supermarket delivery",
                             assigned_to="margaret", priority=2)
    system.tasks.create_task(grace, "Prepare pickup", "Set up the pickup counter for 4 pm", priority=2)
    system.tasks.create_task(grace, "Sort donation", "Check the chilled shelf for dates", priority=3)
    system.stock.record_offer(grace, "Bakery on Main", [
        {"item": "Bread", "quantity": 20, "expiry": days(2)}], dropoff_time="Friday 8 am")
    return system


def reset(data_dir: str | Path) -> FoodShareSystem:
    path = Path(data_dir)
    if path.exists():
        shutil.rmtree(path)
    return seed_demo(path)


if __name__ == "__main__":
    target = Path(__file__).resolve().parent.parent / "data"
    if "--reset" in sys.argv or not (target / "users.json").exists():
        reset(target)
        print(f"Demo data written to {target}")
    else:
        print("Data already exists. Use --reset to replace it with demo data.")
