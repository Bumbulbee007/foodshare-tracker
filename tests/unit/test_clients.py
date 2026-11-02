"""Unit tests: client profiles, consent and data rights (F-03).

Covers: FR-13 to FR-19, FR-35, FR-44, FR-45, NFR-07, NFR-11.
"""

from datetime import timedelta

import pytest

from conftest import add_client
from foodshare.errors import NotFoundError, PermissionDeniedError, ValidationError
from foodshare.models import Visit
from foodshare.services.clients import OPTIONAL_CATEGORIES


# ------------------------------------------------------------- create
def test_profile_saved_exactly(system, staff):                            # AC-11
    c = add_client(system, staff, preferred_name="Amina", adults=1, children=2, infants=1,
                   religious=["halal"], allergies=["tree-nut"],
                   consents={"religious": True, "allergies": True})
    again = system.clients.get_client(staff, c.id)
    assert (again.adults, again.children, again.infants) == (1, 2, 1)
    assert again.religious == ["halal"] and again.allergies == ["tree-nut"]
    assert again.contact_method == "SMS"


@pytest.mark.parametrize("field, value", [("adults", -1), ("children", -1), ("infants", 21),
                                          ("adults", "two"), ("older", 1.5)])
def test_invalid_household_numbers_name_the_field(system, staff, field, value):   # AC-13
    with pytest.raises(ValidationError) as err:
        add_client(system, staff, **{field: value})
    assert err.value.field == field


def test_household_of_zero_rejected(system, staff):                        # BVA 0 members
    with pytest.raises(ValidationError):
        add_client(system, staff, adults=0)


@pytest.mark.parametrize("adults", [1, 20])
def test_household_limits_accepted(system, staff, adults):                 # BVA 1 and 20
    assert add_client(system, staff, adults=adults).adults == adults


def test_older_only_household_is_valid(system, staff):
    assert add_client(system, staff, adults=0, older=1).household_size == 1


@pytest.mark.parametrize("method, detail, ok", [
    ("SMS", "0400 111 222", True), ("SMS", "call me", False), ("Phone call", "+61400111222", True),
    ("Email", "a@b.com", True), ("Email", "ab.com", False), ("In person", "", True),
    ("Pigeon", "", False)])
def test_contact_method_and_detail(system, staff, method, detail, ok):     # FR-16, EP
    if ok:
        c = add_client(system, staff, contact_method=method, contact_detail=detail)
        assert c.contact_method == method
    else:
        with pytest.raises(ValidationError):
            add_client(system, staff, contact_method=method, contact_detail=detail)


def test_in_person_contact_stores_no_detail(system, staff):               # data minimisation
    c = add_client(system, staff, contact_method="In person", contact_detail="0400 000 000")
    assert c.contact_detail == ""


def test_preferred_language_recorded(system, staff):                       # AC-43 (part)
    assert add_client(system, staff, preferred_language="Arabic").preferred_language == "Arabic"


def test_name_required_and_respectful(system, staff):
    with pytest.raises(ValidationError):
        add_client(system, staff, preferred_name="  ")
    with pytest.raises(ValidationError):
        add_client(system, staff, preferred_name="needy family")


# ------------------------------------------------------------ consent
def test_every_optional_category_has_a_purpose():                          # FR-15
    from foodshare.services.clients import ClientService
    for category in OPTIONAL_CATEGORIES:
        assert len(ClientService.consent_purpose(category)) > 20


def test_need_saved_only_with_consent(system, staff):                     # AC-12
    c = add_client(system, staff, medical=["gluten-free"], allergies=["peanut"],
                   consents={"medical": False, "allergies": True})
    assert c.medical == [] and c.allergies == ["peanut"]
    assert c.consents == {"medical": False, "allergies": True}


def test_need_without_any_consent_answer_is_not_saved(system, staff):
    c = add_client(system, staff, dietary=["vegan"])
    assert c.dietary == []


def test_withdrawing_consent_clears_the_field(system, staff):
    c = add_client(system, staff, dietary=["vegan"], consents={"dietary": True})
    c = system.clients.update_client(staff, c.id, {"consents": {"dietary": False}})
    assert c.dietary == []


def test_unknown_option_rejected(system, staff):
    with pytest.raises(ValidationError):
        add_client(system, staff, allergies=["kryptonite"], consents={"allergies": True})


@pytest.mark.parametrize("pin, ok", [("2468", True), ("246", False), ("24680", False), ("abcd", False)])
def test_kiosk_pin_four_digits(system, staff, pin, ok):
    if ok:
        assert add_client(system, staff, kiosk_pin=pin).kiosk_pin_hash
    else:
        with pytest.raises(ValidationError):
            add_client(system, staff, kiosk_pin=pin)


# -------------------------------------------------------- view / audit
def test_viewing_a_profile_is_audited(system, staff):                      # FR-35, AC-31
    c = add_client(system, staff)
    system.clients.get_client(staff, c.id)
    last = system.repo.all("audit")[-1]
    assert (last.user, last.action, last.record) == ("grace", "view", f"client:{c.id}")


def test_volunteer_cannot_open_profile_and_is_audited(system, staff, volunteer):   # AC-30
    c = add_client(system, staff)
    with pytest.raises(PermissionDeniedError):
        system.clients.get_client(volunteer, c.id)
    assert system.repo.all("audit")[-1].outcome == "denied"


def test_volunteer_cannot_list_clients(system, volunteer):
    with pytest.raises(PermissionDeniedError):
        system.clients.list_clients(volunteer)


def test_list_and_search(system, staff):
    add_client(system, staff, preferred_name="Amina")
    add_client(system, staff, preferred_name="Sam")
    assert [r["preferred_name"] for r in system.clients.list_clients(staff, "ami")] == ["Amina"]
    assert len(system.clients.list_clients(staff)) == 2


def test_unknown_client(system, staff):
    with pytest.raises(NotFoundError):
        system.clients.get_client(staff, "C-9999")


# --------------------------------------------------------------- notes
def test_restricted_note(system, staff):                                    # FR-17
    c = add_client(system, staff)
    note = system.clients.add_note(staff, c.id, "Family situation", restricted=True)
    assert note.restricted and system.repo.get("clients", c.id).notes[0].text == "Family situation"


@pytest.mark.parametrize("text", ["", "   ", "A poor family"])
def test_note_must_be_present_and_respectful(system, staff, text):         # NFR-05
    c = add_client(system, staff)
    with pytest.raises(ValidationError):
        system.clients.add_note(staff, c.id, text)


# --------------------------------------------------------- data rights
def test_information_summary_lists_fields_and_consents(system, staff):    # FR-18, AC-44
    c = add_client(system, staff, allergies=["peanut"], consents={"allergies": True, "medical": False})
    system.clients.add_note(staff, c.id, "Prefers mornings")
    text = system.clients.information_summary(staff, c.id)
    for expected in ("Test Household", "0400 000 000", "Allergies: peanut (consent given)",
                     "Medical: - (consent not given)", "Dietary: - (not asked)", "Prefers mornings"):
        assert expected in text
    assert system.repo.all("audit")[-1].detail == "information summary"


def test_correction_replaces_old_value_and_is_audited(system, staff):     # FR-44, AC-45
    c = add_client(system, staff, contact_detail="0400 000 000")
    system.clients.update_client(staff, c.id, {"contact_detail": "0499 999 999"})
    again = system.repo.get("clients", c.id)
    assert again.contact_detail == "0499 999 999"
    assert "0400 000 000" not in str(again.to_dict())
    assert "contact_detail" in system.repo.all("audit")[-1].detail


def test_deletion_keeps_only_deidentified_visits(system, staff):           # FR-45, AC-15
    c = add_client(system, staff, allergies=["peanut"], consents={"allergies": True})
    c.visits = [Visit("2026-10-01", "H-0001"), Visit("2026-10-15", "H-0002")]
    system.clients.add_note(staff, c.id, "Private")
    system.clients.delete_client(staff, c.id)
    kept = system.repo.get("clients", c.id)
    assert kept.deleted and kept.preferred_name == "(deleted)"
    assert kept.contact_detail == "" and kept.allergies == [] and kept.notes == []
    assert len(kept.visits) == 2
    assert system.repo.all("audit")[-1].action == "delete"
    with pytest.raises(NotFoundError):
        system.clients.get_client(staff, c.id)
    assert system.clients.list_clients(staff) == []


@pytest.mark.parametrize("days_ago, flagged", [(729, False), (732, True)])
def test_retention_review_boundary(system, admin, staff, days_ago, flagged):   # NFR-11 BVA ~24 months
    c = add_client(system, staff)
    c.visits = [Visit((system.clock.today() - timedelta(days=days_ago)).isoformat(), "H-0001")]
    assert (c.id in [r["id"] for r in system.clients.retention_review(admin)]) == flagged


def test_retention_review_admin_only(system, staff):
    with pytest.raises(PermissionDeniedError):
        system.clients.retention_review(staff)
