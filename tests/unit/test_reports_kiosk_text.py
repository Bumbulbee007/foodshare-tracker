"""Unit tests: reports (F-07), kiosk (F-08) and respectful language.

Covers: FR-37 to FR-43, NFR-04, NFR-05, NFR-22.
"""

import csv
import io
import json
from datetime import timedelta

import pytest

from conftest import add_client, add_stock, book, full_stock, packed_hamper
from foodshare.errors import (
    AuthenticationError, PermissionDeniedError, SessionExpiredError, ValidationError,
)
from foodshare.text import TEXT_DIR, banned_terms, find_banned_terms, t


# ================================================================ reports
def test_stock_movement_report(system, staff, volunteer):                 # FR-37
    add_stock(system, staff, "Rice", 20)
    bad = add_stock(system, staff, "Lentils", 4)
    system.stock.flag_batch(staff, bad.id, "damaged")
    p, h = packed_hamper(system, staff, volunteer, add_client(system, staff))
    system.hampers.confirm_collection(staff, p.code)
    today = system.clock.today()
    rows = {r["category"]: r for r in system.reports.stock_movement(staff, today, today)["rows"]}
    assert rows["Grains"]["received"] == 20 and rows["Grains"]["distributed"] == 2
    assert rows["Legumes"]["discarded"] == 4


def test_packed_but_uncollected_is_not_distributed(system, staff, volunteer):   # AC-33 rule
    add_stock(system, staff, "Rice", 20)
    packed_hamper(system, staff, volunteer, add_client(system, staff))
    today = system.clock.today()
    rows = system.reports.stock_movement(staff, today, today)["rows"]
    assert rows[0]["distributed"] == 0


def test_demand_report_counts(system, staff, volunteer):                  # FR-38
    add_stock(system, staff, "Rice", 50)
    for _ in range(2):
        c = add_client(system, staff, infants=1)
        p, _ = packed_hamper(system, staff, volunteer, c)
        system.hampers.confirm_collection(staff, p.code)
    today = system.clock.today()
    report = system.reports.demand(staff, today, today)
    assert report["summary"]["households_served"] == 2
    assert report["summary"]["hampers_distributed"] == 2
    assert {"need": "infant needs", "category": "Infant formula", "requests": 2} in report["rows"]


def test_reports_contain_no_identifying_data(system, staff, volunteer):   # FR-39, AC-35
    add_stock(system, staff, "Rice", 20)
    c = add_client(system, staff, preferred_name="Amina Yusuf", contact_detail="0400 123 456")
    p, _ = packed_hamper(system, staff, volunteer, c)
    system.hampers.confirm_collection(staff, p.code)
    today = system.clock.today()
    for report in (system.reports.stock_movement(staff, today, today),
                   system.reports.demand(staff, today, today)):
        text = json.dumps(report) + system.reports.to_csv(report)
        for private in ("Amina", "0400 123 456", p.code, c.id):
            assert private not in text


def test_csv_matches_screen(system, staff):                               # FR-40, AC-36
    add_stock(system, staff, "Rice", 20)
    today = system.clock.today()
    report = system.reports.stock_movement(staff, today, today)
    rows = list(csv.reader(io.StringIO(system.reports.to_csv(report))))
    assert rows[1] == ["category", "unit", "received", "distributed", "discarded", "returned"]
    assert rows[2][:3] == ["Grains", "kg", "20.0"]


def test_empty_range_shows_note(system, staff):                           # AC-37
    day = system.clock.today() - timedelta(days=30)
    report = system.reports.stock_movement(staff, day, day)
    assert report["rows"] == [] and report["note"] == "No activity in this period."
    assert "No activity" in system.reports.to_csv(report)
    assert system.reports.demand(staff, day, day)["note"] == "No activity in this period."


@pytest.mark.parametrize("offset, ok", [(-1, False), (0, True)])
def test_report_range_boundary(system, staff, offset, ok):                # BVA end = start
    start = system.clock.today()
    end = start + timedelta(days=offset)
    if ok:
        assert system.reports.demand(staff, start, end)
    else:
        with pytest.raises(ValidationError):
            system.reports.demand(staff, start, end)


def test_report_bad_date_text(system, staff):
    with pytest.raises(ValidationError):
        system.reports.stock_movement(staff, "yesterday", "today")


def test_volunteer_cannot_run_reports(system, volunteer):
    with pytest.raises(PermissionDeniedError):
        system.reports.demand(volunteer, "2026-10-01", "2026-10-30")


# ================================================================== kiosk
@pytest.fixture
def kiosk_client(system, staff):
    full_stock(system, staff)
    c = add_client(system, staff, kiosk_pin="2468", allergies=["peanut"], consents={"allergies": True})
    system.clients.add_note(staff, c.id, "restricted thing", restricted=True)
    return c, book(system, staff, c)


def test_kiosk_change_slot_is_seen_by_staff(system, kiosk_client):         # FR-41, AC-38
    c, p = kiosk_client
    session = system.kiosk.start(p.code, "2468")
    system.kiosk.choose(session, "slot", "10:00-12:00")
    assert system.kiosk.save(session) == "Saved. Thank you."
    assert system.repo.get("pickups", p.id).slot == "10:00-12:00"


def test_kiosk_shows_only_slot_and_contact(system, kiosk_client):          # AC-42, NFR-22
    c, p = kiosk_client
    choices = system.kiosk.current_choices(system.kiosk.start(p.code, "2468"))
    assert set(choices) == {"date", "slot", "slots", "contact_method", "contact_methods"}
    assert "peanut" not in str(choices) and "restricted" not in str(choices)


def test_three_wrong_pins_then_ask_staff(system, kiosk_client):            # AC-39 BVA
    c, p = kiosk_client
    for _ in range(3):
        with pytest.raises(AuthenticationError):
            system.kiosk.start(p.code, "0000")
    with pytest.raises(AuthenticationError) as err:
        system.kiosk.start(p.code, "2468")                                  # now blocked
    assert "ask a staff member" in str(err.value)


def test_two_wrong_pins_still_allows_entry(system, kiosk_client):
    c, p = kiosk_client
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            system.kiosk.start(p.code, "0000")
    assert system.kiosk.start(p.code, "2468")


def test_kiosk_unknown_code_or_no_pin(system, staff):
    with pytest.raises(AuthenticationError):
        system.kiosk.start("ZZ1", "2468")
    p = book(system, staff, add_client(system, staff))                      # no kiosk PIN set
    with pytest.raises(AuthenticationError):
        system.kiosk.start(p.code, "")


def test_kiosk_resets_after_60_seconds(system, clock, kiosk_client):       # FR-43, AC-41 BVA
    c, p = kiosk_client
    session = system.kiosk.start(p.code, "2468")
    clock.advance(seconds=60)
    system.kiosk.choose(session, "slot", "10:00-12:00")                    # exactly 60 s: fine
    clock.advance(seconds=61)
    with pytest.raises(SessionExpiredError):
        system.kiosk.save(session)
    assert session.pending == {}                                           # unsaved change discarded
    assert system.repo.get("pickups", p.id).slot == "16:00-18:00"


@pytest.mark.parametrize("field, value", [("slot", "01:00-02:00"), ("contact_method", "Fax"),
                                          ("allergies", "none")])
def test_kiosk_rejects_bad_choices(system, kiosk_client, field, value):
    c, p = kiosk_client
    with pytest.raises(ValidationError):
        system.kiosk.choose(system.kiosk.start(p.code, "2468"), field, value)


def test_kiosk_save_with_nothing_changed(system, kiosk_client):
    c, p = kiosk_client
    with pytest.raises(ValidationError):
        system.kiosk.save(system.kiosk.start(p.code, "2468"))


def test_kiosk_contact_change_needs_existing_detail(system, kiosk_client):
    c, p = kiosk_client                                                    # has a phone, no email
    s = system.kiosk.start(p.code, "2468")
    system.kiosk.choose(s, "contact_method", "Email")
    with pytest.raises(ValidationError):
        system.kiosk.save(s)
    system.kiosk.choose(s, "contact_method", "Phone call")
    system.kiosk.save(s)
    assert system.repo.get("clients", c.id).contact_method == "Phone call"


# ======================================================== language checks
def test_language_file_has_no_banned_terms():                              # NFR-05
    raw = (TEXT_DIR / "en.json").read_text(encoding="utf-8")
    assert find_banned_terms(raw) == []


def test_banned_terms_list_matches_srs():
    assert set(banned_terms()) == {"needy", "poor", "charity case", "beneficiary", "welfare recipient"}


@pytest.mark.parametrize("text, found", [("A needy family", ["needy"]), ("NEEDY", ["needy"]),
                                         ("Charity Case", ["charity case"]), ("poorly packed", []),
                                         ("Lentils replaced chickpeas", [])])
def test_banned_terms_match_whole_words_any_case(text, found):
    assert find_banned_terms(text) == found


def test_text_lookup_and_formatting():
    assert t("code_not_found") == "Code not found."
    assert "K27" in t("pickup_message.English", date="2026-10-30", slot="16:00-18:00", code="K27")


def test_screens_never_show_banned_terms():                                  # NFR-05, AC-40 support
    root = TEXT_DIR.parents[1]
    for path in [root / "app.py", *sorted((root / "ui").glob("*.py"))]:
        assert find_banned_terms(path.read_text(encoding="utf-8")) == [], path.name



def test_no_form_or_widget_key_is_also_a_session_state_key():
    """Streamlit forbids writing session_state[key] when a widget or form owns that key."""
    import re
    root = TEXT_DIR.parents[1]
    widget_keys, state_keys = set(), set()
    for path in [root / "app.py", *sorted((root / "ui").glob("*.py"))]:
        source = path.read_text(encoding="utf-8")
        widget_keys |= set(re.findall(r'st\.form\(f?"([^"]+)"', source))
        widget_keys |= set(re.findall(r'key=f?"([^"]+)"', source))
        state_keys |= set(re.findall(r'session_state\["([^"]+)"\]\s*=', source))
    assert widget_keys & state_keys == set()