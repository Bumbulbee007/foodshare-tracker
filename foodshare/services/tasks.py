"""F-05 Volunteer task management (and the packing part of F-04).

Requirements: FR-23, FR-27 to FR-31, FR-46, FR-49, NFR-06.
Use cases: UC-07 (complete packing task), UC-07a (substitution), UC-08.

Volunteers only ever receive a "task view": item lines and dietary tags,
never the client's name, contact details or history (FR-30, AD-06).
"""

from __future__ import annotations

from ..clock import Clock
from ..errors import NotFoundError, PermissionDeniedError, ValidationError
from ..models import (
    HamperStatus, MovementKind, Role, StockMovement, Task, TaskFlag, TaskKind,
    TaskStatus, User,
)
from ..repository import JsonRepository
from ..security import AccessControl, AuditService
from ..text import find_banned_terms
from .hampers import HamperService, hamper_tags, need_label

ISSUE_TYPES = ("damaged item", "missing stock", "incomplete request")


class TaskService:
    def __init__(self, repo: JsonRepository, clock: Clock, access: AccessControl,
                 audit: AuditService, hampers: HamperService):
        self.repo = repo
        self.clock = clock
        self.access = access
        self.audit = audit
        self.hampers = hampers

    # ========================================================= staff side
    def create_task(self, user: User, kind: TaskKind | str, title: str,
                    assigned_to: str = "", priority: int = 2) -> Task:
        """Create a task and assign it or leave it open (FR-27)."""
        self.access.require(user, "task.manage", "tasks")
        kind = TaskKind(kind)
        if not (title or "").strip():
            raise ValidationError("Give the task a short title.", "title")
        if priority not in (1, 2, 3):
            raise ValidationError("Priority must be 1 (high), 2 (normal) or 3 (low).", "priority")
        task = Task(id=self.repo.next_id("tasks"), kind=kind, title=title.strip(),
                    created_by=user.id, created_at=self.clock.now().isoformat(), priority=priority)
        if assigned_to:
            self._check_assignee(assigned_to)
            task.assigned_to, task.status = assigned_to, TaskStatus.ASSIGNED
        self.repo.add("tasks", task)
        self.audit.record(user.id, "create", f"task:{task.id}", detail=f"assigned to {assigned_to or 'open'}")
        return task

    def assign_task(self, user: User, task_id: str, username: str) -> Task:
        """Assign (or with username '' un-assign) a task (FR-27)."""
        self.access.require(user, "task.manage", f"task:{task_id}")
        task = self._get(task_id)
        if task.status == TaskStatus.COMPLETE:
            raise ValidationError("This task is already complete.", "status")
        if username:
            self._check_assignee(username)
        task.assigned_to = username
        task.status = TaskStatus.ASSIGNED if username else TaskStatus.OPEN
        self.repo.add("tasks", task)
        self.audit.record(user.id, "assign", f"task:{task_id}", detail=username or "open")
        return task

    def _check_assignee(self, username: str) -> None:
        person = self.repo.get("users", username)
        if person is None or not person.active or person.role != Role.VOLUNTEER:
            raise ValidationError("Choose an active volunteer account.", "assigned_to")

    def list_volunteers(self, user: User) -> list[str]:
        """Usernames staff can assign tasks to."""
        self.access.require(user, "task.manage", "tasks")
        return sorted(u.id for u in self.repo.all("users") if u.active and u.role == Role.VOLUNTEER)

    def all_tasks(self, user: User, include_complete: bool = False) -> list[Task]:
        self.access.require(user, "task.manage", "tasks")
        tasks = [t for t in self.repo.all("tasks") if include_complete or t.status != TaskStatus.COMPLETE]
        return sorted(tasks, key=lambda t: (t.priority, t.created_at, t.id))

    def open_flags(self, user: User) -> list[dict]:
        """Unresolved issues raised by volunteers, for the staff dashboard (FR-31)."""
        self.access.require(user, "task.manage", "tasks")
        rows = []
        for task in self.repo.all("tasks"):
            for index, flag in enumerate(task.flags):
                if not flag.resolved:
                    rows.append({"task_id": task.id, "index": index, "task": task.title,
                                 "issue_type": flag.issue_type, "note": flag.note,
                                 "raised_by": flag.raised_by, "raised_at": flag.raised_at})
        return sorted(rows, key=lambda r: r["raised_at"])

    def resolve_flag(self, user: User, task_id: str, index: int) -> None:
        self.access.require(user, "task.manage", f"task:{task_id}")
        task = self._get(task_id)
        if not 0 <= index < len(task.flags):
            raise ValidationError("No such issue.", "index")
        task.flags[index].resolved = True
        self.repo.add("tasks", task)
        self.audit.record(user.id, "resolve", f"task:{task_id}", detail=task.flags[index].issue_type)

    # ===================================================== volunteer side
    def my_tasks(self, user: User) -> list[Task]:
        """The user's own tasks plus open tasks, by priority (FR-28, AC-23)."""
        self.access.require(user, "task.work", "tasks")
        tasks = [t for t in self.repo.all("tasks")
                 if t.status != TaskStatus.COMPLETE
                 and (t.assigned_to == user.id or t.status == TaskStatus.OPEN)]
        return sorted(tasks, key=lambda t: (t.priority, t.created_at, t.id))

    def claim_task(self, user: User, task_id: str) -> Task:
        """A volunteer takes an open task."""
        self.access.require(user, "task.work", f"task:{task_id}")
        task = self._get(task_id)
        if task.status != TaskStatus.OPEN:
            raise ValidationError("Someone has already taken this task.", "status")
        task.assigned_to, task.status = user.id, TaskStatus.ASSIGNED
        self.repo.add("tasks", task)
        self.audit.record(user.id, "claim", f"task:{task_id}")
        return task

    def _task_for(self, user: User, task_id: str) -> Task:
        """Load a task the user may work on. Volunteers: own tasks only."""
        self.access.require(user, "task.work", f"task:{task_id}")
        task = self._get(task_id)
        if user.role == Role.VOLUNTEER and task.assigned_to != user.id:
            self.audit.record(user.id, "denied", f"task:{task_id}", outcome="denied",
                              detail="task not assigned to this volunteer")
            raise PermissionDeniedError("task not assigned to you")
        return task

    def task_view(self, user: User, task_id: str) -> dict:
        """Everything a packer needs and nothing more (FR-30, AC-25)."""
        task = self._task_for(user, task_id)
        view = {"id": task.id, "kind": task.kind.value, "title": task.title,
                "priority": task.priority, "status": task.status.value,
                "tags": [], "lines": [], "shortfalls": [],
                "flags": [f.to_dict() for f in task.flags]}
        if task.hamper_id:
            hamper = self.repo.get("hampers", task.hamper_id)
            client = self.repo.get("clients", hamper.client_id)
            view["tags"] = hamper_tags(client)
            view["hamper_status"] = hamper.status.value
            view["lines"] = [{"index": i, "item": l.item, "quantity": l.quantity, "unit": l.unit,
                              "packed": l.packed, "substituted_from": l.substituted_from,
                              "substitution_note": l.substitution_note}
                             for i, l in enumerate(hamper.lines)]
            view["shortfalls"] = [{"category": s["category"], "quantity": s["quantity"],
                                   "unit": s["unit"]} for s in hamper.shortfalls]
        return view

    def set_line_packed(self, user: User, task_id: str, line_index: int, packed: bool = True) -> None:
        task = self._task_for(user, task_id)
        hamper = self._open_hamper(task)
        line = self._line(hamper, line_index)
        line.packed = packed
        self.repo.add("hampers", hamper)

    def substitutes_for(self, user: User, task_id: str, line_index: int) -> list[dict]:
        """The system's safe list of substitutes for one line (FR-49, AC-48)."""
        task = self._task_for(user, task_id)
        hamper = self._open_hamper(task)
        line = self._line(hamper, line_index)
        return [{"batch_id": b.id, "item": b.item, "expiry": b.expiry, "available": b.available}
                for b in self.hampers.safe_substitutes(hamper, line)]

    def substitute_line(self, user: User, task_id: str, line_index: int,
                        batch_id: str, note: str) -> None:
        """Swap an unavailable item for a safe substitute with a note (FR-23, UC-07a)."""
        task = self._task_for(user, task_id)
        hamper = self._open_hamper(task)
        line = self._line(hamper, line_index)
        note = (note or "").strip()
        if not note:
            raise ValidationError("Write a short substitution note for the household.", "note")
        if find_banned_terms(note):
            raise ValidationError("Please reword the note using respectful language.", "note")
        safe = {b.id: b for b in self.hampers.safe_substitutes(hamper, line)}
        if batch_id not in safe:
            raise ValidationError("Choose a substitute from the safe list.", "batch_id")
        old = self.repo.get("batches", line.batch_id)
        new = safe[batch_id]
        old.reserved = round(max(old.reserved - line.quantity, 0.0), 3)
        new.reserved = round(new.reserved + line.quantity, 3)
        self.repo.add("batches", old)
        self.repo.add("batches", new)
        line.substituted_from = line.substituted_from or line.item
        line.item, line.batch_id = new.item, new.id
        line.substitution_note = note
        line.packed = False
        self.repo.add("hampers", hamper)
        self.audit.record(user.id, "substitute", f"hamper:{hamper.id}",
                          detail=f"{line.substituted_from} -> {new.item}")

    def mark_line_unavailable(self, user: User, task_id: str, line_index: int) -> None:
        """No item and no safe substitute: record an unmet request (FR-46)."""
        task = self._task_for(user, task_id)
        hamper = self._open_hamper(task)
        line = self._line(hamper, line_index)
        if self.hampers.safe_substitutes(hamper, line):
            raise ValidationError("A safe substitute is available. Please choose one.", "line")
        batch = self.repo.get("batches", line.batch_id)
        batch.reserved = round(max(batch.reserved - line.quantity, 0.0), 3)
        self.repo.add("batches", batch)
        client = self.repo.get("clients", hamper.client_id)
        rule = self.repo.get("rules", line.category)
        need = need_label(rule, client) if rule else "general"
        hamper.shortfalls.append({"category": line.category, "quantity": line.quantity,
                                  "unit": line.unit, "need": need, "rule_text": line.rule_text})
        self.hampers._record_unmet(hamper, line.category, need, line.quantity, line.unit)
        hamper.lines.pop(line_index)
        self.repo.add("hampers", hamper)
        self.audit.record(user.id, "unavailable", f"hamper:{hamper.id}", detail=line.item)

    def complete_task(self, user: User, task_id: str) -> Task:
        """Finish a task; a packing task deducts packed stock (FR-29, AC-24)."""
        task = self._task_for(user, task_id)
        if task.status == TaskStatus.COMPLETE:
            raise ValidationError("This task is already complete.", "status")
        if task.kind == TaskKind.PACK and task.hamper_id:
            hamper = self._open_hamper(task)
            if not all(line.packed for line in hamper.lines):
                raise ValidationError("Tick every item as packed, or choose a substitute, "
                                      "before completing the task.", "lines")
            today = self.clock.today().isoformat()
            for line in hamper.lines:
                batch = self.repo.get("batches", line.batch_id)
                batch.quantity = round(batch.quantity - line.quantity, 3)
                batch.reserved = round(max(batch.reserved - line.quantity, 0.0), 3)
                self.repo.add("batches", batch)
                self.repo.add("movements", StockMovement(
                    id=self.repo.next_id("movements"), date=today, batch_id=batch.id,
                    item=batch.item, category=batch.category, quantity=line.quantity,
                    kind=MovementKind.PACKED, reference=hamper.id))
            hamper.status = HamperStatus.PACKED
            self.repo.add("hampers", hamper)
        task.status = TaskStatus.COMPLETE
        task.completed_by = user.id
        task.completed_at = self.clock.now().isoformat()
        self.repo.add("tasks", task)
        self.audit.record(user.id, "complete", f"task:{task_id}")
        return task

    def flag_issue(self, user: User, task_id: str, issue_type: str, note: str = "") -> TaskFlag:
        """Raise an issue on a task, shown on the staff dashboard (FR-31, AC-26)."""
        task = self._task_for(user, task_id)
        if issue_type not in ISSUE_TYPES:
            raise ValidationError("Choose an issue type.", "issue_type")
        flag = TaskFlag(issue_type=issue_type, note=(note or "").strip(),
                        raised_by=user.id, raised_at=self.clock.now().isoformat())
        task.flags.append(flag)
        self.repo.add("tasks", task)
        self.audit.record(user.id, "flag", f"task:{task_id}", detail=issue_type)
        return flag

    # ============================================================ helpers
    def _get(self, task_id: str) -> Task:
        task = self.repo.get("tasks", task_id)
        if task is None:
            raise NotFoundError(f"Task {task_id} not found.")
        return task

    def _open_hamper(self, task: Task):
        if not task.hamper_id:
            raise ValidationError("This task has no hamper.", "task")
        hamper = self.repo.get("hampers", task.hamper_id)
        if hamper.status != HamperStatus.CONFIRMED:
            raise ValidationError("This hamper is no longer waiting to be packed.", "status")
        return hamper

    @staticmethod
    def _line(hamper, index: int):
        if not 0 <= index < len(hamper.lines):
            raise ValidationError("Choose an item from the list.", "line")
        return hamper.lines[index]
