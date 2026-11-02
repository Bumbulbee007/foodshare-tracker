"""Functional tests: one test per acceptance criterion (SRS Table 23).

Each test logs in as a real role and drives the system the way that user
would, through the same service calls the screens make. The test name
starts with its FT number and the AC it verifies, so the traceability
matrix (SRS Section 14) can point straight at it.

Test design techniques used: equivalence partitioning (EP), boundary value
analysis (BVA) and, for the hamper-safety decision, MC/DC (see the unit
tests in tests/unit/test_hampers.py).
"""

from datetime import timedelta

import pytest

from conftest import add_client, add_stock, book, full_stock, packed_hamper
from foodshare.errors import (
    AuthenticationError, NotFoundError, PermissionDeniedError, SessionExpiredError, ValidationError,
)
from foodshare.models import MovementKind, Visit


def login(system, username):
    secrets = {"admin": "Admin#2026!", "grace": "Grace#2026!", "margaret": "1234", "david": "5678"}
    return system.auth.login(username, secrets[username]).user


def days(system, n):
    return (system.clock.today() + timedelta(days=n)).isoformat()


# ====================================================== F-01 Donations
class TestF01Donations:
    def test_ft01_ac01_record_donation_appears_in_stock(self, system):
        grace = login(system, "grace")
        system.stock.record_donation(grace, [{"item": "Rice", "quantity": 20, "expiry": days(system, 30),
                                              "storage": "ambient"}])
        batches = system.stock.list_batches(grace)
        assert [(b.item, b.quantity, b.unit, b.storage) for b in batches] == [("Rice", 20, "kg", "ambient")]

    def test_ft02_ac02_expired_donation_rejected(self, system):          # EP invalid, BVA -1 day
        grace = login(system, "grace")
        with pytest.raises(ValidationError, match="passed its expiry date and cannot be accepted"):
            system.stock.record_donation(grace, [{"item": "Rice", "quantity": 5, "expiry": days(system, -1),
                                                  "storage": "ambient"}])
        assert system.stock.list_batches(grace) == []

    def test_ft03_ac03_expires_today_accepted_and_highlighted(self, system):   # BVA 0 days
        grace = login(system, "grace")
        system.stock.record_donation(grace, [{"item": "UHT milk", "quantity": 2, "expiry": days(system, 0),
                                              "storage": "ambient"}])
        assert [b.item for b in system.stock.expiring_soon(grace)] == ["UHT milk"]

    def test_ft04_ac04_offer_form_shows_priority_needs(self, system):
        grace = login(system, "grace")
        system.stock.set_priority_needs(grace, ["Infant formula", "Rice"])
        info = system.stock.priority_needs()
        assert info["needs"] == ["Infant formula", "Rice"] and info["guidance"]
        offer = system.stock.record_offer(grace, "Bakery on Main",
                                          [{"item": "Bread", "quantity": 20, "expiry": days(system, 2)}])
        assert offer in system.stock.list_offers(grace)

    def test_ft05_ac05_anonymous_donation_confirmation(self, system):
        grace = login(system, "grace")
        d = system.stock.record_donation(grace, [{"item": "Rice", "quantity": 1, "expiry": days(system, 9),
                                                  "storage": "ambient"}])
        text = system.stock.confirmation_text(d)
        assert d.id in text and d.received_on in text and "Rice" in text
        assert (d.donor_name, d.donor_contact) == ("", "")
        d2 = system.stock.record_donation(grace, [{"item": "Rice", "quantity": 1, "expiry": days(system, 9),
                                                   "storage": "ambient"}])
        assert d2.id != d.id                                             # unique reference


# ===================================================== F-02 Stock
class TestF02Stock:
    def test_ft06_ac06_totals_and_nearest_expiry(self, system):
        grace = login(system, "grace")
        add_stock(system, grace, "Rice", 10, days=2)
        add_stock(system, grace, "Rice", 5, days=32)
        rice = next(r for r in system.stock.stock_summary(grace) if r["item"] == "Rice")
        assert (rice["on_hand"], rice["nearest_expiry"]) == (15, days(system, 2))

    def test_ft07_ac07_threshold_boundary(self, system):                 # BVA 7 / 8
        grace = login(system, "grace")
        on = add_stock(system, grace, "Rice", 1, days=7)
        off = add_stock(system, grace, "Rice", 1, days=8)
        ids = [b.id for b in system.stock.expiring_soon(grace)]
        assert on.id in ids and off.id not in ids

    def test_ft08_ac08_fefo(self, system):
        grace = login(system, "grace")
        soon = add_stock(system, grace, "Rice", 10, days=2)
        add_stock(system, grace, "Rice", 5, days=32)
        h = system.hampers.generate_hamper(grace, book(system, grace, add_client(system, grace)).code)
        assert [(l.batch_id, l.quantity) for l in h.lines if l.item == "Rice"] == [(soon.id, 2)]

    @pytest.mark.parametrize("tins, alert", [(10, False), (9, True)])
    def test_ft09_ac09_low_stock_alert_boundary(self, system, tins, alert):   # BVA 10 / 9
        grace = login(system, "grace")
        add_stock(system, grace, "Infant formula", tins)                   # minimum level is 10
        alerts = [a["item"] for a in system.stock.low_stock_alerts(grace)]
        assert ("Infant formula" in alerts) is alert

    def test_ft10_ac10_volunteer_flags_damaged_batch(self, system):
        grace, margaret = login(system, "grace"), login(system, "margaret")
        b = add_stock(system, grace, "Canned tomatoes", 12)
        with pytest.raises(ValidationError):
            system.stock.flag_batch(margaret, b.id, "")                   # no reason
        system.stock.flag_batch(margaret, b.id, "damaged", "dented can")
        flagged = system.stock.flagged_batches(grace)[0]
        assert (flagged.flagged_by, flagged.flag_reason) == ("margaret", "damaged: dented can")
        assert flagged.flagged_at and b.id not in [x.id for x in system.stock.list_batches(grace)]


# ===================================================== F-03 Profiles
class TestF03Profiles:
    def test_ft11_ac11_profile_round_trip(self, system):
        grace = login(system, "grace")
        c = add_client(system, grace, preferred_name="Amina", adults=1, children=2, infants=1,
                       religious=["halal"], allergies=["tree-nut"],
                       consents={"religious": True, "allergies": True})
        again = system.clients.get_client(grace, c.id)
        assert (again.preferred_name, again.contact_method, again.adults, again.children, again.infants,
                again.religious, again.allergies) == ("Amina", "SMS", 1, 2, 1, ["halal"], ["tree-nut"])

    def test_ft12_ac12_consent_gates_optional_field(self, system):
        grace = login(system, "grace")
        assert "medical" in system.clients.consent_purpose("medical")
        c = add_client(system, grace, medical=["gluten-free"], consents={"medical": False})
        assert c.medical == [] and c.preferred_name == "Test Household"

    @pytest.mark.parametrize("field, value", [("adults", 0), ("children", -1)])
    def test_ft13_ac13_invalid_household_blocked(self, system, field, value):   # EP invalid / BVA 0
        grace = login(system, "grace")
        with pytest.raises(ValidationError) as err:
            add_client(system, grace, **{field: value})
        assert err.value.field == field

    def test_ft14_ac14_restricted_note_and_history_hidden_from_volunteers(self, system):
        grace, margaret = login(system, "grace"), login(system, "margaret")
        c = add_client(system, grace)
        system.clients.add_note(grace, c.id, "Private context", restricted=True)
        c.visits = [Visit("2026-10-01", "H-1"), Visit("2026-10-08", "H-2"), Visit("2026-10-15", "H-3")]
        assert len(system.clients.get_client(grace, c.id).visits) == 3
        with pytest.raises(PermissionDeniedError):
            system.clients.get_client(margaret, c.id)

    def test_ft15_ac15_deletion(self, system):
        grace = login(system, "grace")
        c = add_client(system, grace, allergies=["peanut"], consents={"allergies": True})
        c.visits = [Visit("2026-10-01", "H-1")]
        system.clients.delete_client(grace, c.id)
        kept = system.repo.get("clients", c.id)
        assert kept.deleted and kept.allergies == [] and len(kept.visits) == 1
        assert system.repo.all("audit")[-1].action == "delete"

    def test_ft16_ac43_language_preference_and_english_fallback(self, system):
        grace = login(system, "grace")
        c = add_client(system, grace, preferred_language="Arabic")
        assert system.clients.get_client(grace, c.id).preferred_language == "Arabic"
        msg = system.hampers.pickup_message(grace, book(system, grace, c).id)
        assert (msg["method"], msg["language"]) == ("SMS", "English")

    def test_ft17_ac44_show_client_information(self, system):
        grace = login(system, "grace")
        c = add_client(system, grace, allergies=["egg"], consents={"allergies": True})
        text = system.clients.information_summary(grace, c.id)
        assert "egg (consent given)" in text and "0400 000 000" in text
        assert system.repo.all("audit")[-1].action == "view"

    def test_ft18_ac45_correct_phone_number(self, system):
        grace = login(system, "grace")
        c = add_client(system, grace, contact_detail="0400 000 000")
        system.clients.update_client(grace, c.id, {"contact_detail": "0411 111 111"})
        assert system.clients.get_client(grace, c.id).contact_detail == "0411 111 111"
        assert any(e.action == "update" for e in system.repo.all("audit"))


# ===================================================== F-04 Hampers
class TestF04Hampers:
    def test_ft19_ac16_rule_quantity_and_explanation(self, system):
        grace = login(system, "grace")
        full_stock(system, grace)
        c = add_client(system, grace, adults=2, children=1)
        h = system.hampers.generate_hamper(grace, book(system, grace, c).code)
        rice = [l for l in h.lines if l.category == "Grains"]
        assert sum(l.quantity for l in rice) == 5 and "2 kg per adult, 1 kg per child" in rice[0].rule_text

    def test_ft20_ac17_halal_household(self, system):
        grace = login(system, "grace")
        full_stock(system, grace)
        add_stock(system, grace, "Canned beef", 10, days=3)
        c = add_client(system, grace, religious=["halal"], consents={"religious": True})
        h = system.hampers.generate_hamper(grace, book(system, grace, c).code)
        protein = [l.item for l in h.lines if l.category == "Protein"]
        assert protein and "Canned beef" not in protein

    def test_ft21_ac18_peanut_allergy_never_packed(self, system):
        grace = login(system, "grace")
        full_stock(system, grace)
        add_stock(system, grace, "Peanut butter", 30, days=2)      # soonest expiry: FEFO would pick it
        c = add_client(system, grace, adults=2, allergies=["peanut"], consents={"allergies": True})
        h = system.hampers.generate_hamper(grace, book(system, grace, c).code)
        assert "Peanut butter" not in [l.item for l in h.lines]

    def test_ft22_ac19_substitution_required_before_completion(self, system):
        grace, margaret = login(system, "grace"), login(system, "margaret")
        add_stock(system, grace, "Chickpeas", 1, days=10)
        lentils = add_stock(system, grace, "Lentils", 5, days=40)
        h = system.hampers.generate_hamper(grace, book(system, grace, add_client(system, grace)).code)
        task = system.hampers.confirm_hamper(grace, h.id)
        system.tasks.assign_task(grace, task.id, "margaret")
        # Chickpeas turn out to be missing from the shelf: packer cannot tick it
        with pytest.raises(ValidationError):
            system.tasks.complete_task(margaret, task.id)
        index = [l["item"] for l in system.tasks.task_view(margaret, task.id)["lines"]].index("Chickpeas")
        system.tasks.substitute_line(margaret, task.id, index, lentils.id,
                                     "Lentils replaced chickpeas this week as chickpeas are out of stock.")
        for i in range(len(system.tasks.task_view(margaret, task.id)["lines"])):
            system.tasks.set_line_packed(margaret, task.id, i)
        system.tasks.complete_task(margaret, task.id)
        assert any(l.substitution_note for l in system.repo.get("hampers", h.id).lines)

    def test_ft23_ac20_pickup_code_and_message(self, system):
        grace = login(system, "grace")
        c = add_client(system, grace, preferred_name="Amina", allergies=["peanut"], consents={"allergies": True},
                       infants=1)
        thursday = system.clock.today() + timedelta(days=(3 - system.clock.today().weekday()) % 7)
        p = system.hampers.book_pickup(grace, c.id, thursday, "16:00-18:00")
        text = system.hampers.pickup_message(grace, p.id)["text"]
        assert p.code in text and "16:00-18:00" in text
        assert not any(w in text for w in ("Amina", "peanut", "infant", "household"))

    def test_ft24_ac21_counter_collection_and_unknown_code(self, system):
        grace, margaret = login(system, "grace"), login(system, "margaret")
        full_stock(system, grace)
        p, h = packed_hamper(system, grace, margaret, add_client(system, grace))
        system.hampers.confirm_collection(grace, p.code)
        assert system.clients.get_client(grace, h.client_id).visits[-1].hamper_id == h.id
        assert {m.reference for m in system.repo.all("movements") if m.kind == MovementKind.DISTRIBUTED} == {h.id}
        with pytest.raises(NotFoundError, match="^Code not found.$"):
            system.hampers.confirm_collection(grace, "Z99")

    def test_ft25_ac46_unmet_infant_need(self, system):
        grace = login(system, "grace")
        add_stock(system, grace, "Rice", 10)
        c = add_client(system, grace, infants=1)
        h = system.hampers.generate_hamper(grace, book(system, grace, c).code)
        system.hampers.confirm_hamper(grace, h.id)
        assert ("infant needs", "Infant formula") in {(u.need, u.category) for u in system.repo.all("unmet")}

    def test_ft26_ac47_uncollected_hamper_returned(self, system, clock):
        grace, margaret = login(system, "grace"), login(system, "margaret")
        rice = add_stock(system, grace, "Rice", 10)
        p, h = packed_hamper(system, grace, margaret, add_client(system, grace))
        clock.advance(hours=10)                                    # end of the pickup day
        system.hampers.return_uncollected(grace, h.id)
        assert system.repo.get("batches", rice.id).quantity == 10
        today = system.clock.today()
        report = system.reports.stock_movement(grace, today, today)
        assert all(r["distributed"] == 0 for r in report["rows"])

    def test_ft27_ac48_safe_substitute_list(self, system):
        grace, margaret = login(system, "grace"), login(system, "margaret")
        add_stock(system, grace, "Chickpeas", 1)
        add_stock(system, grace, "Lentils", 5)
        add_stock(system, grace, "Kidney beans", 5)
        add_stock(system, grace, "Almond spread", 5)
        c = add_client(system, grace, allergies=["tree-nut"], consents={"allergies": True})
        h = system.hampers.generate_hamper(grace, book(system, grace, c).code)
        task = system.hampers.confirm_hamper(grace, h.id)
        system.tasks.assign_task(grace, task.id, "margaret")
        index = [l.item for l in system.repo.get("hampers", h.id).lines].index("Chickpeas")
        options = {o["item"] for o in system.tasks.substitutes_for(margaret, task.id, index)}
        assert options == {"Lentils", "Kidney beans"}

    def test_ft28_ac49_negative_rule_rejected_staff_locked_out(self, system):
        admin, grace = login(system, "admin"), login(system, "grace")
        rules = [r.to_dict() for r in system.hampers.list_rules(admin)]
        for r in rules:
            if r["id"] == "Grains":
                r["per_adult"] = -1
        with pytest.raises(ValidationError):
            system.hampers.save_rules(admin, rules)
        assert system.repo.get("rules", "Grains").per_adult == 2
        with pytest.raises(PermissionDeniedError):
            system.hampers.save_rules(grace, rules)

    def test_ft29_ac50_adjustment_needs_reason_and_is_audited(self, system):
        grace = login(system, "grace")
        full_stock(system, grace)
        h = system.hampers.generate_hamper(grace, book(system, grace, add_client(system, grace)).code)
        with pytest.raises(ValidationError):
            system.hampers.adjust_line(grace, h.id, 0, 0, "")
        system.hampers.adjust_line(grace, h.id, 0, 0, "Client asked for less cereal")
        assert "Client asked for less cereal" in system.repo.all("audit")[-1].detail


# ===================================================== F-05 Tasks
class TestF05Tasks:
    def test_ft30_ac22_task_assigned_to_one_volunteer(self, system):
        grace, margaret, david = (login(system, u) for u in ("grace", "margaret", "david"))
        t = system.tasks.create_task(grace, "Pack hamper", "Pack hamper H-0412", "margaret")
        assert t.id in [x.id for x in system.tasks.my_tasks(margaret)]
        assert t.id not in [x.id for x in system.tasks.my_tasks(david)]

    def test_ft31_ac23_exactly_five_tasks_in_priority_order(self, system):
        grace, margaret = login(system, "grace"), login(system, "margaret")
        for p in (2, 3, 1):
            system.tasks.create_task(grace, "Sort donation", f"mine {p}", "margaret", p)
        for p in (1, 3):
            system.tasks.create_task(grace, "Sort donation", f"open {p}", "", p)
        for n in range(4):
            system.tasks.create_task(grace, "Sort donation", f"other {n}", "david")
        listed = system.tasks.my_tasks(margaret)
        assert len(listed) == 5 and [t.priority for t in listed] == [1, 1, 2, 3, 3]

    def test_ft32_ac24_packing_deducts_stock(self, system):
        grace, margaret = login(system, "grace"), login(system, "margaret")
        rice = add_stock(system, grace, "Rice", 20)
        packed_hamper(system, grace, margaret, add_client(system, grace, adults=2, children=1))
        assert system.repo.get("batches", rice.id).quantity == 15

    def test_ft33_ac25_packer_sees_tags_not_identity(self, system):
        grace, margaret = login(system, "grace"), login(system, "margaret")
        full_stock(system, grace)
        c = add_client(system, grace, preferred_name="Amina Yusuf", infants=1, religious=["halal"],
                       allergies=["tree-nut"], consents={"religious": True, "allergies": True})
        h = system.hampers.generate_hamper(grace, book(system, grace, c).code)
        task = system.hampers.confirm_hamper(grace, h.id)
        system.tasks.assign_task(grace, task.id, "margaret")
        view = system.tasks.task_view(margaret, task.id)
        assert set(view["tags"]) == {"halal", "tree-nut-free", "infant"}
        assert "Amina" not in str(view) and "0400" not in str(view)

    def test_ft34_ac26_issue_flag_reaches_staff(self, system):
        grace, margaret = login(system, "grace"), login(system, "margaret")
        t = system.tasks.create_task(grace, "Sort donation", "Sort delivery", "margaret")
        with pytest.raises(ValidationError):
            system.tasks.flag_issue(margaret, t.id, "")
        system.tasks.flag_issue(margaret, t.id, "missing stock")
        assert system.tasks.open_flags(grace)[0]["issue_type"] == "missing stock"


# ===================================================== F-06 Access
class TestF06Access:
    def test_ft35_ac28_lockout_after_five_failures(self, system):
        for _ in range(5):
            with pytest.raises(AuthenticationError):
                system.auth.login("grace", "nope")
        with pytest.raises(AuthenticationError) as err:
            system.auth.login("grace", "Grace#2026!")
        with pytest.raises(AuthenticationError) as unknown:
            system.auth.login("nobody", "nope")
        assert str(err.value) == str(unknown.value)              # does not reveal the username exists

    def test_ft36_ac29_admin_creates_and_deactivates_volunteer(self, system):
        admin = login(system, "admin")
        system.users.create_user(admin, "sue", "Volunteer", "4321")
        sue = system.auth.login("sue", "4321").user
        assert system.tasks.my_tasks(sue) == []
        with pytest.raises(PermissionDeniedError):
            system.clients.list_clients(sue)
        system.users.set_active(admin, "sue", False)
        with pytest.raises(AuthenticationError):
            system.auth.login("sue", "4321")

    @pytest.mark.parametrize("route", ["get_client", "information_summary", "update_client", "delete_client"])
    def test_ft37_ac30_volunteer_denied_on_every_route(self, system, route):
        grace, margaret = login(system, "grace"), login(system, "margaret")
        c = add_client(system, grace)
        args = (margaret, c.id, {}) if route == "update_client" else (margaret, c.id)
        with pytest.raises(PermissionDeniedError):
            getattr(system.clients, route)(*args)
        last = system.repo.all("audit")[-1]
        assert (last.user, last.outcome) == ("margaret", "denied")

    def test_ft38_ac31_view_is_audited(self, system):
        grace = login(system, "grace")
        c = add_client(system, grace)
        system.clients.get_client(grace, c.id)
        e = system.repo.all("audit")[-1]
        assert (e.user, e.action, e.record) == ("grace", "view", f"client:{c.id}") and e.timestamp

    def test_ft39_ac32_audit_filter(self, system, clock):
        admin, grace = login(system, "admin"), login(system, "grace")
        c = add_client(system, grace)
        system.clients.get_client(grace, c.id)
        rows = system.audit_log(admin, username="grace", start=clock.today() - timedelta(days=7), end=clock.today())
        assert rows and all(r.user == "grace" for r in rows)
        with pytest.raises(PermissionDeniedError):
            system.audit_log(grace)

    def test_ft40_nfr09_idle_session_ends(self, system, clock):
        session = system.auth.login("grace", "Grace#2026!")
        clock.advance(minutes=11)
        with pytest.raises(SessionExpiredError):
            system.auth.touch(session)


# ===================================================== F-07 Reports
class TestF07Reports:
    def test_ft41_ac33_stock_movement_september_example(self, system, clock):
        from datetime import datetime
        clock.set(datetime(2026, 9, 1, 9, 0))
        grace, margaret = login(system, "grace"), login(system, "margaret")
        add_stock(system, grace, "Rice", 100, days=90)
        bad = add_stock(system, grace, "Rice", 5, days=90)
        system.stock.flag_batch(grace, bad.id, "damaged")
        # 30 households collect 2 kg each = 60 kg distributed; 5 more packed but not collected = 10 kg
        for n in range(35):
            p, _ = packed_hamper(system, grace, margaret, add_client(system, grace))
            if n < 30:
                system.hampers.confirm_collection(grace, p.code)
        report = system.reports.stock_movement(grace, "2026-09-01", "2026-09-30")
        grains = next(r for r in report["rows"] if r["category"] == "Grains")
        assert (grains["received"], grains["distributed"], grains["discarded"]) == (105, 60, 5)

    def test_ft42_ac34_demand_report(self, system):
        grace, margaret = login(system, "grace"), login(system, "margaret")
        add_stock(system, grace, "Rice", 100)
        for _ in range(3):
            c = add_client(system, grace, religious=["halal"], consents={"religious": True})
            p, _ = packed_hamper(system, grace, margaret, c)
            system.hampers.confirm_collection(grace, p.code)
        today = system.clock.today()
        report = system.reports.demand(grace, today, today)
        assert report["summary"]["households_served"] == 3 and report["summary"]["hampers_distributed"] == 3
        assert report["summary"]["unmet_requests"] > 0

    def test_ft43_ac35_ac36_deidentified_csv(self, system):
        grace, margaret = login(system, "grace"), login(system, "margaret")
        add_stock(system, grace, "Rice", 10)
        c = add_client(system, grace, preferred_name="Priya Sharma")
        p, _ = packed_hamper(system, grace, margaret, c)
        system.hampers.confirm_collection(grace, p.code)
        today = system.clock.today()
        csv_text = system.reports.to_csv(system.reports.demand(grace, today, today))
        assert csv_text.splitlines()[0].startswith("Demand report")
        assert "Priya" not in csv_text and p.code not in csv_text

    def test_ft44_ac37_empty_and_reversed_ranges(self, system):
        grace = login(system, "grace")
        assert system.reports.demand(grace, "2020-01-01", "2020-01-31")["note"] == "No activity in this period."
        with pytest.raises(ValidationError):
            system.reports.demand(grace, "2026-10-30", "2026-10-01")


# ===================================================== F-08 Kiosk
class TestF08Kiosk:
    def setup_kiosk(self, system):
        grace = login(system, "grace")
        c = add_client(system, grace, kiosk_pin="2468", allergies=["peanut"], consents={"allergies": True})
        return grace, c, book(system, grace, c)

    def test_ft45_ac38_client_changes_pickup_time(self, system):
        grace, c, p = self.setup_kiosk(system)
        session = system.kiosk.start(p.code, "2468")
        system.kiosk.choose(session, "slot", "10:00-12:00")
        system.kiosk.save(session)
        assert [x.slot for x in system.hampers.client_pickups(grace, c.id)] == ["10:00-12:00"]

    def test_ft46_ac39_three_wrong_pins(self, system):
        grace, c, p = self.setup_kiosk(system)
        for _ in range(3):
            with pytest.raises(AuthenticationError, match="ask a staff member"):
                system.kiosk.start(p.code, "1111")
        with pytest.raises(AuthenticationError):
            system.kiosk.start(p.code, "2468")

    def test_ft47_ac41_timeout_discards_unsaved(self, system, clock):
        grace, c, p = self.setup_kiosk(system)
        session = system.kiosk.start(p.code, "2468")
        system.kiosk.choose(session, "slot", "10:00-12:00")
        clock.advance(seconds=61)
        assert system.kiosk.is_expired(session)
        with pytest.raises(SessionExpiredError):
            system.kiosk.save(session)
        assert system.hampers.client_pickups(grace, c.id)[0].slot == "16:00-18:00"

    def test_ft48_ac42_kiosk_never_shows_sensitive_data(self, system):
        grace, c, p = self.setup_kiosk(system)
        shown = system.kiosk.current_choices(system.kiosk.start(p.code, "2468"))
        assert "peanut" not in str(shown) and "visits" not in shown and "notes" not in shown
