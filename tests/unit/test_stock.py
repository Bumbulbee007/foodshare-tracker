"""Unit tests: donations and stock (F-01, F-02).

Covers: FR-01 to FR-12. Techniques: equivalence partitioning (EP) and
boundary value analysis (BVA) on expiry dates, quantities and thresholds.
"""

from datetime import timedelta

import pytest

from conftest import add_stock
from foodshare.errors import NotFoundError, PermissionDeniedError, ValidationError
from foodshare.models import BatchStatus, MovementKind


def line(system, item="Rice", quantity=5, days=30, storage="ambient"):
    return {"item": item, "quantity": quantity,
            "expiry": (system.clock.today() + timedelta(days=days)).isoformat(), "storage": storage}


# ------------------------------------------------------- record donation
def test_donation_creates_batch_and_movement(system, staff):
    d = system.stock.record_donation(staff, [line(system, quantity=20)])
    b = system.repo.get("batches", d.lines[0].batch_id)
    assert (b.item, b.quantity, b.unit, b.storage) == ("Rice", 20, "kg", "ambient")
    assert system.repo.all("movements")[0].kind == MovementKind.RECEIVED


def test_each_line_becomes_its_own_batch(system, staff):                 # FR-07
    d = system.stock.record_donation(staff, [line(system, days=10), line(system, days=40)])
    assert len({l.batch_id for l in d.lines}) == 2


@pytest.mark.parametrize("days, accepted", [(-1, False), (0, True), (1, True)])
def test_expiry_boundary(system, staff, days, accepted):                  # FR-04 BVA, AC-02/03
    if accepted:
        assert system.stock.record_donation(staff, [line(system, days=days)])
    else:
        with pytest.raises(ValidationError) as err:
            system.stock.record_donation(staff, [line(system, days=days)])
        assert "passed its expiry date" in str(err.value)
        assert system.repo.all("batches") == []


@pytest.mark.parametrize("qty", [0, -1, "abc", None])
def test_invalid_quantity_rejected(system, staff, qty):                   # EP invalid
    with pytest.raises(ValidationError) as err:
        system.stock.record_donation(staff, [line(system, quantity=qty)])
    assert err.value.field == "quantity"


def test_smallest_positive_quantity_accepted(system, staff):             # BVA just above 0
    assert system.stock.record_donation(staff, [line(system, quantity=0.001)])


@pytest.mark.parametrize("storage", ["", "warm", None])
def test_invalid_storage_rejected(system, staff, storage):
    with pytest.raises(ValidationError):
        system.stock.record_donation(staff, [line(system, storage=storage)])


@pytest.mark.parametrize("storage", ["ambient", "Chilled", "FROZEN"])
def test_storage_types_accepted_any_case(system, staff, storage):
    assert system.stock.record_donation(staff, [line(system, storage=storage)])


def test_bad_date_format_rejected(system, staff):
    bad = line(system)
    bad["expiry"] = "30/11/2026"
    with pytest.raises(ValidationError) as err:
        system.stock.record_donation(staff, [bad])
    assert err.value.field == "expiry"


def test_unknown_item_rejected(system, staff):
    with pytest.raises(NotFoundError):
        system.stock.record_donation(staff, [line(system, item="Caviar")])


def test_empty_donation_rejected(system, staff):
    with pytest.raises(ValidationError):
        system.stock.record_donation(staff, [])


def test_one_bad_line_saves_nothing(system, staff):                       # all-or-nothing
    with pytest.raises(ValidationError):
        system.stock.record_donation(staff, [line(system), line(system, days=-5)])
    assert system.repo.all("batches") == [] and system.repo.all("donations") == []


def test_anonymous_donation_stores_no_donor_fields(system, staff):        # FR-06, AC-05
    d = system.stock.record_donation(staff, [line(system)])
    assert d.donor_name == "" and d.donor_contact == ""


def test_confirmation_has_reference_date_and_items(system, staff):        # FR-05
    d = system.stock.record_donation(staff, [line(system, quantity=3)])
    text = system.stock.confirmation_text(d)
    assert d.id in text and d.received_on in text and "Rice: 3 kg" in text


def test_volunteer_cannot_record_donation(system, volunteer):
    with pytest.raises(PermissionDeniedError):
        system.stock.record_donation(volunteer, [line(system)])


# ------------------------------------------------------------ offers
def test_offer_recorded_with_guidance_available(system, staff):           # FR-02, FR-03
    needs = system.stock.priority_needs()
    assert needs["needs"] == ["Infant formula", "Rice"] and "home-cooked" in needs["guidance"]
    offer = system.stock.record_offer(staff, "Bakery", [line(system, item="Bread")], "Fri 8am")
    assert offer.items[0]["item"] == "Bread"   # offers may name items not yet catalogued


@pytest.mark.parametrize("donor, items", [("", [{"item": "Bread", "quantity": 1, "expiry": "2026-12-01"}]),
                                          ("Bakery", []),
                                          ("Bakery", [{"item": " ", "quantity": 1, "expiry": "2026-12-01"}])])
def test_offer_needs_donor_and_items(system, staff, donor, items):
    with pytest.raises(ValidationError):
        system.stock.record_offer(staff, donor, items)


def test_priority_needs_can_be_changed(system, staff):
    system.stock.set_priority_needs(staff, ["Nappies", " ", "Oil"])
    assert system.stock.priority_needs()["needs"] == ["Nappies", "Oil"]


def test_list_offers(system, staff):
    system.stock.record_offer(staff, "Bakery", [line(system, item="Bread")])
    assert len(system.stock.list_offers(staff)) == 1


# -------------------------------------------------------- stock views
def test_summary_totals_and_nearest_expiry(system, staff):               # FR-08, AC-06
    add_stock(system, staff, "Rice", 10, days=2)
    add_stock(system, staff, "Rice", 5, days=32)
    row = next(r for r in system.stock.stock_summary(staff) if r["item"] == "Rice")
    assert row["on_hand"] == 15
    assert row["nearest_expiry"] == (system.clock.today() + timedelta(days=2)).isoformat()


@pytest.mark.parametrize("days, highlighted", [(6, True), (7, True), (8, False)])
def test_expiring_soon_boundary(system, staff, days, highlighted):         # FR-09 BVA, AC-07
    b = add_stock(system, staff, "Rice", 1, days=days)
    assert (b.id in [x.id for x in system.stock.expiring_soon(staff)]) == highlighted


def test_threshold_is_configurable(system, staff):
    add_stock(system, staff, "Rice", 1, days=12)
    system.stock.set_expiry_threshold(staff, 14)
    assert len(system.stock.expiring_soon(staff)) == 1


@pytest.mark.parametrize("days", [-1, 91, 2.5])
def test_threshold_must_be_0_to_90_whole_days(system, staff, days):
    with pytest.raises(ValidationError):
        system.stock.set_expiry_threshold(staff, days)


@pytest.mark.parametrize("qty, alert", [(11, False), (10, False), (9, True)])
def test_low_stock_alert_boundary(system, staff, qty, alert):             # FR-11 BVA, AC-09
    add_stock(system, staff, "Infant formula", qty)                        # min level 10
    alerts = [a["item"] for a in system.stock.low_stock_alerts(staff)]
    assert ("Infant formula" in alerts) == alert


def test_non_essential_item_never_alerts(system, staff):
    assert "Pasta" not in [a["item"] for a in system.stock.low_stock_alerts(staff)]


def test_set_essential_rejects_negative_minimum(system, staff):
    with pytest.raises(ValidationError):
        system.stock.set_essential(staff, "Pasta", True, -1)
    assert system.stock.set_essential(staff, "Pasta", True, 3).min_level == 3


# ---------------------------------------------------------- flagging
def test_flag_removes_batch_from_stock(system, staff, volunteer):         # FR-12, AC-10
    b = add_stock(system, staff, "Rice", 8)
    system.stock.flag_batch(volunteer, b.id, "damaged", "split bag")
    b = system.repo.get("batches", b.id)
    assert b.status == BatchStatus.DAMAGED and b.available == 0
    assert b.flagged_by == "margaret" and "split bag" in b.flag_reason
    assert system.repo.all("movements")[-1].kind == MovementKind.DISCARDED
    assert b.id not in [x.id for x in system.stock.list_batches(staff)]
    assert b.id in [x.id for x in system.stock.flagged_batches(staff)]


@pytest.mark.parametrize("reason", ["", "lost", None])
def test_flag_needs_valid_reason(system, staff, reason):
    b = add_stock(system, staff, "Rice", 8)
    with pytest.raises(ValidationError):
        system.stock.flag_batch(staff, b.id, reason)


def test_flag_twice_rejected(system, staff):
    b = add_stock(system, staff, "Rice", 8)
    system.stock.flag_batch(staff, b.id, "missing")
    with pytest.raises(ValidationError):
        system.stock.flag_batch(staff, b.id, "damaged")


def test_flag_unknown_batch(system, staff):
    with pytest.raises(NotFoundError):
        system.stock.flag_batch(staff, "B-9999", "damaged")


def test_reserved_batch_must_be_flagged_from_task(system, staff):
    b = add_stock(system, staff, "Rice", 8)
    b.reserved = 2
    with pytest.raises(ValidationError):
        system.stock.flag_batch(staff, b.id, "damaged")


# ---------------------------------------------------------- catalogue
def test_add_item_with_tags(system, staff):
    item = system.stock.add_item(staff, "Couscous", "Grains", "kg", ["gluten"], ["vegan", "halal"])
    assert item.allergens == ["gluten"] and system.stock.get_item("Couscous")


@pytest.mark.parametrize("name, category, unit", [("Rice", "Grains", "kg"), ("", "Grains", "kg"),
                                                  ("Oil", "Oils", "L"), ("Couscous", "Grains", "tin")])
def test_add_item_validation(system, staff, name, category, unit):
    with pytest.raises(ValidationError):
        system.stock.add_item(staff, name, category, unit)
