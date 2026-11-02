"""Unit tests: volunteer tasks, packing and substitution (F-05, part of F-04).

Covers: FR-23, FR-27 to FR-31, FR-46, FR-49, NFR-06.
"""

import pytest

from conftest import add_client, add_stock, book, full_stock
from foodshare.errors import PermissionDeniedError, ValidationError
from foodshare.models import HamperStatus, MovementKind, TaskStatus


def confirmed_task(system, staff, volunteer, **client_kw):
    c = add_client(system, staff, **client_kw)
    p = book(system, staff, c)
    h = system.hampers.generate_hamper(staff, p.code)
    task = system.hampers.confirm_hamper(staff, h.id)
    system.tasks.assign_task(staff, task.id, volunteer.id)
    return task, system.repo.get("hampers", h.id)


# ------------------------------------------------------- staff: tasks
def test_create_assigned_task(system, staff, volunteer):                  # FR-27, AC-22
    t = system.tasks.create_task(staff, "Sort donation", "Sort supermarket delivery", "margaret")
    assert t.status == TaskStatus.ASSIGNED and t.assigned_to == "margaret"


def test_create_open_task(system, staff):
    assert system.tasks.create_task(staff, "Prepare pickup", "Counter set-up").status == TaskStatus.OPEN


@pytest.mark.parametrize("kind, title, assignee, priority", [
    ("Sort donation", "", "", 2), ("Sort donation", "x", "grace", 2),      # staff is not a volunteer
    ("Sort donation", "x", "ghost", 2), ("Sort donation", "x", "", 0), ("Sort donation", "x", "", 4)])
def test_create_task_validation(system, staff, kind, title, assignee, priority):
    with pytest.raises(ValidationError):
        system.tasks.create_task(staff, kind, title, assignee, priority)


def test_unknown_task_kind(system, staff):
    with pytest.raises(ValueError):
        system.tasks.create_task(staff, "Juggle", "x")


def test_volunteer_cannot_create_tasks(system, volunteer):
    with pytest.raises(PermissionDeniedError):
        system.tasks.create_task(volunteer, "Sort donation", "x")


def test_my_tasks_shows_own_and_open_by_priority(system, staff, volunteer):   # FR-28, AC-23
    for p in (3, 1, 2):
        system.tasks.create_task(staff, "Sort donation", f"mine p{p}", "margaret", p)
    for p in (2, 1):
        system.tasks.create_task(staff, "Prepare pickup", f"open p{p}", "", p)
    for n in range(4):
        system.tasks.create_task(staff, "Sort donation", f"david {n}", "david")
    tasks = system.tasks.my_tasks(volunteer)
    assert len(tasks) == 5
    assert [t.priority for t in tasks] == sorted(t.priority for t in tasks)
    assert all(t.assigned_to in ("margaret", "") for t in tasks)


def test_completed_tasks_disappear_from_list(system, staff, volunteer):
    t = system.tasks.create_task(staff, "Sort donation", "x", "margaret")
    system.tasks.complete_task(volunteer, t.id)
    assert system.tasks.my_tasks(volunteer) == []
    assert system.repo.get("tasks", t.id).completed_by == "margaret"


def test_claim_open_task_once(system, staff, volunteer, volunteer2):
    t = system.tasks.create_task(staff, "Prepare pickup", "Counter")
    system.tasks.claim_task(volunteer, t.id)
    with pytest.raises(ValidationError):
        system.tasks.claim_task(volunteer2, t.id)


def test_reassign_and_unassign(system, staff):
    t = system.tasks.create_task(staff, "Sort donation", "x", "margaret")
    assert system.tasks.assign_task(staff, t.id, "david").assigned_to == "david"
    assert system.tasks.assign_task(staff, t.id, "").status == TaskStatus.OPEN


# ------------------------------------------------- volunteer task view
def test_task_view_has_tags_and_items_but_no_identity(system, staff, volunteer):   # FR-30, AC-25
    full_stock(system, staff)
    task, hamper = confirmed_task(system, staff, volunteer, preferred_name="Amina Yusuf",
                                  contact_detail="0400 111 222", infants=1,
                                  religious=["halal"], allergies=["tree-nut"],
                                  consents={"religious": True, "allergies": True})
    view = system.tasks.task_view(volunteer, task.id)
    assert view["tags"] == ["halal", "tree-nut-free", "infant"]
    assert len(view["lines"]) == len(hamper.lines)
    text = str(view)
    for private in ("Amina", "0400", hamper.client_id, "visits", "notes"):
        assert private not in text


def test_volunteer_cannot_open_someone_elses_task(system, staff, volunteer, volunteer2):   # NFR-06
    full_stock(system, staff)
    task, _ = confirmed_task(system, staff, volunteer)
    with pytest.raises(PermissionDeniedError):
        system.tasks.task_view(volunteer2, task.id)
    assert system.repo.all("audit")[-1].outcome == "denied"


def test_staff_can_open_any_task(system, staff, volunteer):
    full_stock(system, staff)
    task, _ = confirmed_task(system, staff, volunteer)
    assert system.tasks.task_view(staff, task.id)["id"] == task.id


# ------------------------------------------------------------ packing
def test_complete_blocked_until_every_line_packed(system, staff, volunteer):
    full_stock(system, staff)
    task, hamper = confirmed_task(system, staff, volunteer)
    system.tasks.set_line_packed(volunteer, task.id, 0)
    with pytest.raises(ValidationError):
        system.tasks.complete_task(volunteer, task.id)


def test_completing_pack_task_deducts_stock(system, staff, volunteer):      # FR-29, AC-24
    rice = add_stock(system, staff, "Rice", 10)
    task, hamper = confirmed_task(system, staff, volunteer, adults=2, children=1)   # 5 kg
    for i in range(len(hamper.lines)):
        system.tasks.set_line_packed(volunteer, task.id, i)
    done = system.tasks.complete_task(volunteer, task.id)
    rice = system.repo.get("batches", rice.id)
    assert (rice.quantity, rice.reserved) == (5, 0)
    assert done.status == TaskStatus.COMPLETE and done.completed_at
    assert system.repo.get("hampers", hamper.id).status == HamperStatus.PACKED
    assert MovementKind.PACKED in [m.kind for m in system.repo.all("movements")]


def test_cannot_complete_twice(system, staff, volunteer):
    t = system.tasks.create_task(staff, "Sort donation", "x", "margaret")
    system.tasks.complete_task(volunteer, t.id)
    with pytest.raises(ValidationError):
        system.tasks.complete_task(volunteer, t.id)


def test_bad_line_index(system, staff, volunteer):
    full_stock(system, staff)
    task, _ = confirmed_task(system, staff, volunteer)
    with pytest.raises(ValidationError):
        system.tasks.set_line_packed(volunteer, task.id, 99)


# ------------------------------------------------------- substitution
def legumes_setup(system, staff, volunteer):
    add_stock(system, staff, "Chickpeas", 1, days=10)
    lentils = add_stock(system, staff, "Lentils", 5, days=40)
    add_stock(system, staff, "Almond spread", 5, days=40)
    task, hamper = confirmed_task(system, staff, volunteer, allergies=["tree-nut"],
                                  consents={"allergies": True})
    index = next(i for i, l in enumerate(hamper.lines) if l.item == "Chickpeas")
    return task, hamper, index, lentils


def test_safe_list_has_only_same_category_safe_items(system, staff, volunteer):   # FR-49, AC-48
    task, hamper, index, lentils = legumes_setup(system, staff, volunteer)
    options = system.tasks.substitutes_for(volunteer, task.id, index)
    assert [o["item"] for o in options] == ["Lentils"]


def test_substitution_needs_note_from_safe_list(system, staff, volunteer):   # FR-23, AC-19
    task, hamper, index, lentils = legumes_setup(system, staff, volunteer)
    with pytest.raises(ValidationError):
        system.tasks.substitute_line(volunteer, task.id, index, lentils.id, "")
    with pytest.raises(ValidationError):
        system.tasks.substitute_line(volunteer, task.id, index, "B-9999", "note")
    with pytest.raises(ValidationError):
        system.tasks.substitute_line(volunteer, task.id, index, lentils.id, "For a needy family")
    system.tasks.substitute_line(volunteer, task.id, index, lentils.id,
                                 "Lentils replaced chickpeas this week.")
    line = system.repo.get("hampers", hamper.id).lines[index]
    assert (line.item, line.substituted_from) == ("Lentils", "Chickpeas")
    assert line.substitution_note.startswith("Lentils replaced")
    assert system.repo.get("batches", lentils.id).reserved == 1


def test_mark_unavailable_blocked_when_substitute_exists(system, staff, volunteer):
    task, hamper, index, _ = legumes_setup(system, staff, volunteer)
    with pytest.raises(ValidationError):
        system.tasks.mark_line_unavailable(volunteer, task.id, index)


def test_no_safe_substitute_records_unmet_request(system, staff, volunteer):   # FR-46
    add_stock(system, staff, "Infant formula", 2)
    task, hamper = confirmed_task(system, staff, volunteer, infants=1)
    index = next(i for i, l in enumerate(hamper.lines) if l.item == "Infant formula")
    system.tasks.mark_line_unavailable(volunteer, task.id, index)
    unmet = system.repo.all("unmet")[-1]
    assert (unmet.category, unmet.need) == ("Infant formula", "infant needs")
    assert "Infant formula" not in [l.item for l in system.repo.get("hampers", hamper.id).lines]


# --------------------------------------------------------------- flags
def test_flag_issue_visible_to_staff(system, staff, volunteer):           # FR-31, AC-26
    t = system.tasks.create_task(staff, "Sort donation", "x", "margaret")
    system.tasks.flag_issue(volunteer, t.id, "missing stock", "No rice on shelf B")
    flags = system.tasks.open_flags(staff)
    assert flags[0]["issue_type"] == "missing stock" and flags[0]["raised_by"] == "margaret"
    system.tasks.resolve_flag(staff, t.id, 0)
    assert system.tasks.open_flags(staff) == []


@pytest.mark.parametrize("issue", ["", "other", None])
def test_flag_needs_issue_type(system, staff, volunteer, issue):
    t = system.tasks.create_task(staff, "Sort donation", "x", "margaret")
    with pytest.raises(ValidationError):
        system.tasks.flag_issue(volunteer, t.id, issue)
