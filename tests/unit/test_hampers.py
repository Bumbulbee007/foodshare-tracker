"""Unit tests: allocation, hamper generation, pickups (F-04).

Covers: FR-10, FR-20 to FR-26, FR-46 to FR-51, NFR-21, NFR-22.
Includes MC/DC tables for the two safety decisions:
  is_item_eligible = (A) no allergen match AND (B) meets every diet tag
  is_batch_usable  = (A) available status AND (B) quantity left AND (C) in date
"""

import random
from datetime import date, timedelta

import pytest

from conftest import add_client, add_stock, book, full_stock, make_system, packed_hamper
from foodshare.errors import NotFoundError, PermissionDeniedError, ValidationError
from foodshare.models import (
    AllocationRule, BatchStatus, Client, HamperStatus, Item, MovementKind,
    PickupStatus, StockBatch, TaskKind,
)
from foodshare.services.hampers import (
    hamper_tags, is_batch_usable, is_item_eligible, need_label, required_quantity,
)


def client(**kw):
    data = dict(id="C-1", preferred_name="A", contact_method="SMS")
    data.update(kw)
    return Client(**data)


# ============================================== pure decision functions
# MC/DC for is_item_eligible: each condition shown to change the outcome alone.
@pytest.mark.parametrize("case, allergens, item_tags, expected", [
    ("A=T,B=T", [], ["halal"], True),           # baseline: eligible
    ("A=F,B=T", ["peanut"], ["halal"], False),   # only A changes -> outcome changes
    ("A=T,B=F", [], [], False),                  # only B changes -> outcome changes
])
def test_item_eligibility_mcdc(case, allergens, item_tags, expected):
    household = client(religious=["halal"], allergies=["peanut"])
    item = Item("X", "Spreads", "jar", allergens=allergens, dietary_tags=item_tags)
    assert is_item_eligible(item, household) is expected


def test_household_without_needs_accepts_any_item():
    assert is_item_eligible(Item("PB", "Spreads", "jar", ["peanut"], []), client())


def test_every_requirement_tag_must_be_present():
    household = client(religious=["halal"], medical=["gluten-free"])
    assert not is_item_eligible(Item("X", "Grains", "kg", [], ["halal"]), household)
    assert is_item_eligible(Item("X", "Grains", "kg", [], ["halal", "gluten-free"]), household)


TODAY = date(2026, 10, 30)


def stock_batch(**kw):
    data = dict(id="B-1", item="Rice", category="Grains", quantity=5, unit="kg",
                expiry="2026-11-30", storage="ambient", received_on="2026-10-30")
    data.update(kw)
    return StockBatch(**data)


@pytest.mark.parametrize("case, kw, expected", [
    ("A=T,B=T,C=T", {}, True),
    ("A=F", {"status": BatchStatus.DAMAGED}, False),
    ("B=F", {"reserved": 5}, False),
    ("C=F", {"expiry": "2026-10-29"}, False),
])
def test_batch_usable_mcdc(case, kw, expected):
    assert is_batch_usable(stock_batch(**kw), TODAY) is expected


def test_batch_expiring_today_is_usable():                                  # BVA
    assert is_batch_usable(stock_batch(expiry="2026-10-30"), TODAY)


@pytest.mark.parametrize("household, expected", [
    ({"adults": 2, "children": 1}, 5),        # AC-16: 2 x 2 + 1 x 1
    ({"adults": 0, "older": 1}, 2),           # older members count as adults
    ({"adults": 1, "infants": 3}, 2),         # infants get nothing from this rule
])
def test_required_quantity_rice(household, expected):
    rule = AllocationRule("Grains", "kg", per_adult=2, per_child=1)
    assert required_quantity(rule, client(**household)) == expected


def test_count_units_round_down():                                         # Table 15
    rule = AllocationRule("Spreads", "jar", per_adult=0.5, per_child=0.5)
    assert required_quantity(rule, client(adults=1, children=2)) == 1     # 1.5 -> 1
    assert required_quantity(rule, client(adults=1)) == 0                 # 0.5 -> 0


def test_need_labels():
    infant_rule = AllocationRule("Infant formula", "tin", per_infant=2)
    general_rule = AllocationRule("Grains", "kg", per_adult=2)
    assert need_label(infant_rule, client()) == "infant needs"
    assert need_label(general_rule, client()) == "general"
    assert need_label(general_rule, client(religious=["halal"], allergies=["peanut"])) == "halal, peanut-free"


def test_hamper_tags_show_needs_not_identity():
    tags = hamper_tags(client(religious=["halal"], allergies=["tree-nut"], infants=1))
    assert tags == ["halal", "tree-nut-free", "infant"]


# ======================================================= allocation rules
def test_save_valid_rules(system, admin):
    rules = [r.to_dict() for r in system.hampers.list_rules(admin)]
    rules[0]["per_adult"] = 3
    saved = system.hampers.save_rules(admin, rules)
    assert saved[0].per_adult == 3


@pytest.mark.parametrize("change, field", [
    ({"per_adult": -1}, "per_adult"), ({"per_child": "x"}, "per_child"),
    ({"unit": ""}, "unit"), ({"id": ""}, "id")])
def test_invalid_rule_set_rejected_and_old_rules_kept(system, admin, change, field):   # FR-50, AC-49
    before = [r.to_dict() for r in system.hampers.list_rules(admin)]
    edited = [dict(r) for r in before]
    edited[0].update(change)
    with pytest.raises(ValidationError) as err:
        system.hampers.save_rules(admin, edited)
    assert err.value.field == field
    assert [r.to_dict() for r in system.hampers.list_rules(admin)] == before


def test_rule_set_missing_a_used_category_rejected(system, admin):
    rules = [r.to_dict() for r in system.hampers.list_rules(admin) if r.id != "Grains"]
    with pytest.raises(ValidationError) as err:
        system.hampers.save_rules(admin, rules)
    assert "Grains" in str(err.value)


def test_duplicate_rule_rejected(system, admin):
    rules = [r.to_dict() for r in system.hampers.list_rules(admin)]
    with pytest.raises(ValidationError):
        system.hampers.save_rules(admin, rules + [rules[0]])


def test_staff_can_read_but_not_edit_rules(system, staff):
    assert system.hampers.list_rules(staff)
    with pytest.raises(PermissionDeniedError):
        system.hampers.save_rules(staff, [])


# ================================================================ pickups
def test_book_pickup_creates_unique_code(system, staff):                    # FR-24
    codes = {book(system, staff, add_client(system, staff)).code for _ in range(30)}
    assert len(codes) == 30
    assert all(len(c) == 3 and c[0].isalpha() and c[1:].isdigit() for c in codes)


@pytest.mark.parametrize("days, slot, field", [(-1, "16:00-18:00", "date"), (0, "03:00-04:00", "slot")])
def test_book_pickup_rejects_past_date_or_bad_slot(system, staff, days, slot, field):
    with pytest.raises(ValidationError) as err:
        book(system, staff, add_client(system, staff), days=days, slot=slot)
    assert err.value.field == field


def test_one_booked_pickup_per_household_per_day(system, staff):
    c = add_client(system, staff)
    book(system, staff, c)
    with pytest.raises(ValidationError):
        book(system, staff, c, slot="10:00-12:00")
    assert book(system, staff, c, days=1)


def test_book_for_unknown_client(system, staff):
    with pytest.raises(NotFoundError):
        system.hampers.book_pickup(staff, "C-9999", system.clock.today(), "16:00-18:00")


def test_pickup_message_has_only_slot_and_code(system, staff):              # FR-25, AC-20
    c = add_client(system, staff, preferred_name="Amina Yusuf", allergies=["peanut"],
                   consents={"allergies": True})
    p = book(system, staff, c)
    msg = system.hampers.pickup_message(staff, p.id)
    assert p.code in msg["text"] and p.slot in msg["text"]
    for private in ("Amina", "peanut", "0400"):
        assert private not in msg["text"]


def test_pickup_message_falls_back_to_english(system, staff):               # AC-43
    c = add_client(system, staff, preferred_language="Arabic")
    msg = system.hampers.pickup_message(staff, book(system, staff, c).id)
    assert msg == {"method": "SMS", "language": "English", "text": msg["text"]}
    assert msg["text"].startswith("Your pickup is ready")


# ======================================================== generate hamper
def test_hamper_quantity_follows_rule_and_shows_it(system, staff):          # FR-22, AC-16
    full_stock(system, staff)
    c = add_client(system, staff, adults=2, children=1)
    h = system.hampers.generate_hamper(staff, book(system, staff, c).code)
    grains = [l for l in h.lines if l.category == "Grains"]
    assert sum(l.quantity for l in grains) == 5
    assert grains[0].rule_text == "Grains: 2 kg per adult, 1 kg per child"
    assert h.status == HamperStatus.SUGGESTED


def test_fefo_takes_soonest_expiry_first(system, staff):                    # FR-10, AC-08
    later = add_stock(system, staff, "Rice", 5, days=60)
    sooner = add_stock(system, staff, "Rice", 10, days=10)
    c = add_client(system, staff, adults=1)
    h = system.hampers.generate_hamper(staff, book(system, staff, c).code)
    grains = [l for l in h.lines if l.category == "Grains"]
    assert [(l.batch_id, l.quantity) for l in grains] == [(sooner.id, 2)]
    assert later.id not in [l.batch_id for l in h.lines]


def test_fefo_spans_batches_when_first_runs_out(system, staff):
    first = add_stock(system, staff, "Rice", 1, days=5)
    second = add_stock(system, staff, "Rice", 5, days=50)
    h = system.hampers.generate_hamper(staff, book(system, staff, add_client(system, staff)).code)
    grains = [(l.batch_id, l.quantity) for l in h.lines if l.category == "Grains"]
    assert grains == [(first.id, 1), (second.id, 1)]


def test_expired_and_flagged_stock_never_allocated(system, staff, clock):
    old = add_stock(system, staff, "Rice", 5, days=1)
    bad = add_stock(system, staff, "Rice", 5, days=30)
    system.stock.flag_batch(staff, bad.id, "damaged")
    clock.advance(days=2)                                     # old batch now expired
    h = system.hampers.generate_hamper(staff, book(system, staff, add_client(system, staff)).code)
    assert {old.id, bad.id}.isdisjoint(l.batch_id for l in h.lines)


def test_halal_household_gets_only_halal_items(system, staff):              # AC-17
    full_stock(system, staff)
    add_stock(system, staff, "Canned beef", 20, days=5)       # not halal, expires first
    c = add_client(system, staff, adults=2, religious=["halal"], consents={"religious": True})
    h = system.hampers.generate_hamper(staff, book(system, staff, c).code)
    items = {i.id: i for i in system.repo.all("items")}
    assert all("halal" in items[l.item].dietary_tags for l in h.lines)
    assert "Canned beef" not in [l.item for l in h.lines]


def test_no_hamper_ever_contains_a_household_allergen(tmp_path, clock):     # AC-18 (50 households)
    system = make_system(tmp_path / "d", clock)
    staff = system.repo.get("users", "grace")
    full_stock(system, staff)
    add_stock(system, staff, "Almond spread", 30, days=5)
    add_stock(system, staff, "Breakfast cereal", 30, days=5)
    add_stock(system, staff, "Pasta", 30, days=5)
    rnd = random.Random(2026)
    allergens = ["peanut", "tree-nut", "dairy", "gluten", "soy", "fish"]
    items = {i.id: i for i in system.repo.all("items")}
    for n in range(50):
        chosen = rnd.sample(allergens, rnd.randint(1, 3))
        c = add_client(system, staff, adults=rnd.randint(1, 3), children=rnd.randint(0, 3),
                       allergies=chosen, consents={"allergies": True})
        h = system.hampers.generate_hamper(staff, book(system, staff, c).code)
        for l in h.lines:
            assert not set(items[l.item].allergens) & set(chosen), (n, l.item, chosen)


def test_shortfall_recorded_as_unmet_on_confirm(system, staff):             # FR-46, AC-46
    add_stock(system, staff, "Rice", 10)
    c = add_client(system, staff, adults=1, infants=1)
    h = system.hampers.generate_hamper(staff, book(system, staff, c).code)
    gap = next(s for s in h.shortfalls if s["category"] == "Infant formula")
    assert gap["need"] == "infant needs" and gap["quantity"] == 2
    assert system.repo.all("unmet") == []            # not yet: only a suggestion
    system.hampers.confirm_hamper(staff, h.id)
    unmet = system.repo.all("unmet")
    assert {(u.category, u.need) for u in unmet} >= {("Infant formula", "infant needs")}


def test_same_inputs_give_same_hamper(tmp_path, clock):                     # NFR-21
    results = []
    for run in range(2):
        system = make_system(tmp_path / f"run{run}", clock)
        staff = system.repo.get("users", "grace")
        full_stock(system, staff)
        c = add_client(system, staff, adults=2, children=2, allergies=["dairy"], consents={"allergies": True})
        h = system.hampers.generate_hamper(staff, book(system, staff, c).code)
        results.append([(l.item, l.batch_id, l.quantity) for l in h.lines])
    assert results[0] == results[1]


def test_second_hamper_for_same_pickup_rejected(system, staff):
    full_stock(system, staff)
    p = book(system, staff, add_client(system, staff))
    system.hampers.generate_hamper(staff, p.code)
    with pytest.raises(ValidationError):
        system.hampers.generate_hamper(staff, p.code)


def test_generate_with_unknown_code(system, staff):
    with pytest.raises(NotFoundError):
        system.hampers.generate_hamper(staff, "ZZ9")


# ================================================================ adjust
def test_adjust_needs_reason_and_is_audited(system, staff):                 # FR-51, AC-50
    full_stock(system, staff)
    h = system.hampers.generate_hamper(staff, book(system, staff, add_client(system, staff)).code)
    with pytest.raises(ValidationError):
        system.hampers.adjust_line(staff, h.id, 0, 0, "  ")
    count = len(h.lines)
    removed = h.lines[0].item
    h = system.hampers.adjust_line(staff, h.id, 0, 0, "Household already has plenty")
    assert len(h.lines) == count - 1 and h.adjustments[0]["reason"] == "Household already has plenty"
    last = system.repo.all("audit")[-1]
    assert last.action == "adjust" and removed in last.detail


@pytest.mark.parametrize("qty", [-1, 10_000, "lots"])
def test_adjust_quantity_bounds(system, staff, qty):
    full_stock(system, staff)
    h = system.hampers.generate_hamper(staff, book(system, staff, add_client(system, staff)).code)
    with pytest.raises(ValidationError):
        system.hampers.adjust_line(staff, h.id, 0, qty, "reason")


def test_adjust_bad_line_index(system, staff):
    full_stock(system, staff)
    h = system.hampers.generate_hamper(staff, book(system, staff, add_client(system, staff)).code)
    with pytest.raises(ValidationError):
        system.hampers.adjust_line(staff, h.id, 99, 1, "reason")


# =============================================================== confirm
def test_confirm_reserves_stock_and_creates_pack_task(system, staff):
    b = add_stock(system, staff, "Rice", 10)
    h = system.hampers.generate_hamper(staff, book(system, staff, add_client(system, staff)).code)
    task = system.hampers.confirm_hamper(staff, h.id)
    assert system.repo.get("batches", b.id).reserved == 2
    assert system.repo.get("batches", b.id).quantity == 10        # not deducted until packed
    assert task.kind == TaskKind.PACK and task.hamper_id == h.id
    assert system.repo.get("hampers", h.id).status == HamperStatus.CONFIRMED
    with pytest.raises(ValidationError):
        system.hampers.confirm_hamper(staff, h.id)                  # twice


def test_confirm_fails_if_stock_changed(system, staff):
    b = add_stock(system, staff, "Rice", 2)
    h1 = system.hampers.generate_hamper(staff, book(system, staff, add_client(system, staff)).code)
    h2 = system.hampers.generate_hamper(staff, book(system, staff, add_client(system, staff)).code)
    system.hampers.confirm_hamper(staff, h1.id)
    with pytest.raises(ValidationError) as err:
        system.hampers.confirm_hamper(staff, h2.id)
    assert "generate it again" in str(err.value)
    assert system.repo.get("batches", b.id).reserved == 2           # nothing extra reserved


def test_empty_hamper_cannot_be_confirmed(system, staff):
    h = system.hampers.generate_hamper(staff, book(system, staff, add_client(system, staff)).code)
    with pytest.raises(ValidationError):
        system.hampers.confirm_hamper(staff, h.id)


def test_cancel_confirmed_hamper_releases_stock(system, staff):
    b = add_stock(system, staff, "Rice", 10)
    p = book(system, staff, add_client(system, staff))
    h = system.hampers.generate_hamper(staff, p.code)
    task = system.hampers.confirm_hamper(staff, h.id)
    system.hampers.cancel_hamper(staff, h.id)
    assert system.repo.get("batches", b.id).reserved == 0
    assert system.repo.get("tasks", task.id).status.value == "complete"
    assert system.hampers.generate_hamper(staff, p.code)            # pickup can get a new one


# ========================================================= counter / pickup
def test_counter_lookup_shows_no_name(system, staff, volunteer):           # NFR-22
    full_stock(system, staff)
    c = add_client(system, staff, preferred_name="Amina Yusuf")
    p, h = packed_hamper(system, staff, volunteer, c)
    view = system.hampers.counter_lookup(staff, p.code.lower())
    assert view["code"] == p.code and view["hamper_status"] == "packed"
    assert "Amina" not in str(view) and c.id not in str(view)


def test_collection_counts_distributed_and_adds_visit(system, staff, volunteer):   # FR-26, FR-47, AC-21
    full_stock(system, staff)
    c = add_client(system, staff)
    p, h = packed_hamper(system, staff, volunteer, c)
    system.hampers.confirm_collection(staff, p.code)
    assert system.repo.get("hampers", h.id).status == HamperStatus.COLLECTED
    assert system.repo.get("pickups", p.id).status == PickupStatus.COLLECTED
    assert [v.hamper_id for v in system.repo.get("clients", c.id).visits] == [h.id]
    distributed = [m for m in system.repo.all("movements") if m.kind == MovementKind.DISTRIBUTED]
    assert len(distributed) == len(h.lines)


def test_unknown_code_reveals_nothing(system, staff):                      # AC-21 invalid
    with pytest.raises(NotFoundError) as err:
        system.hampers.confirm_collection(staff, "Q11")
    assert str(err.value) == "Code not found."


def test_cannot_collect_unpacked_hamper(system, staff):
    full_stock(system, staff)
    p = book(system, staff, add_client(system, staff))
    system.hampers.confirm_hamper(staff, system.hampers.generate_hamper(staff, p.code).id)
    with pytest.raises(ValidationError):
        system.hampers.confirm_collection(staff, p.code)


def test_collected_code_cannot_be_used_twice(system, staff, volunteer):
    full_stock(system, staff)
    p, _ = packed_hamper(system, staff, volunteer, add_client(system, staff))
    system.hampers.confirm_collection(staff, p.code)
    with pytest.raises(NotFoundError):
        system.hampers.confirm_collection(staff, p.code)


def test_return_packed_hamper_restores_stock(system, staff, volunteer):    # FR-48, AC-47
    b = add_stock(system, staff, "Rice", 10)
    p, h = packed_hamper(system, staff, volunteer, add_client(system, staff))
    assert system.repo.get("batches", b.id).quantity == 8
    system.hampers.return_uncollected(staff, h.id)
    assert system.repo.get("batches", b.id).quantity == 10
    assert system.repo.get("hampers", h.id).status == HamperStatus.RETURNED
    kinds = [m.kind for m in system.repo.all("movements")]
    assert MovementKind.RETURNED in kinds and MovementKind.DISTRIBUTED not in kinds


def test_return_confirmed_hamper_releases_reservation(system, staff):
    b = add_stock(system, staff, "Rice", 10)
    p = book(system, staff, add_client(system, staff))
    h = system.hampers.generate_hamper(staff, p.code)
    system.hampers.confirm_hamper(staff, h.id)
    system.hampers.return_uncollected(staff, h.id)
    assert system.repo.get("batches", b.id).reserved == 0


def test_cannot_return_before_pickup_day(system, staff, volunteer):
    add_stock(system, staff, "Rice", 10)
    p, h = packed_hamper(system, staff, volunteer, add_client(system, staff), days=1)
    with pytest.raises(ValidationError):
        system.hampers.return_uncollected(staff, h.id)


def test_volunteer_cannot_generate_or_collect(system, staff, volunteer):
    with pytest.raises(PermissionDeniedError):
        system.hampers.generate_hamper(volunteer, "A11")
    with pytest.raises(PermissionDeniedError):
        system.hampers.confirm_collection(volunteer, "A11")
