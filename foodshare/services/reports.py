"""F-07 Reporting: de-identified figures and CSV export.

Requirements: FR-37 to FR-40. Use cases: UC-15, UC-15a.

The report pipeline follows a pipe-and-filter shape (SRS Table 35):
collect movements -> filter by date -> aggregate -> (no identifying fields
ever enter the output) -> display or export.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date

from ..clock import Clock
from ..errors import ValidationError
from ..models import HamperStatus, MovementKind, User
from ..repository import JsonRepository
from ..security import AccessControl, AuditService
from ..text import t


def _check_range(start, end) -> tuple[str, str]:
    try:
        start = start if isinstance(start, date) else date.fromisoformat(str(start))
        end = end if isinstance(end, date) else date.fromisoformat(str(end))
    except ValueError:
        raise ValidationError("Enter dates as YYYY-MM-DD.", "start")
    if end < start:                                              # AC-37
        raise ValidationError("The end date is earlier than the start date.", "end")
    return start.isoformat(), end.isoformat()


class ReportService:
    def __init__(self, repo: JsonRepository, clock: Clock, access: AccessControl, audit: AuditService):
        self.repo = repo
        self.clock = clock
        self.access = access
        self.audit = audit

    def stock_movement(self, user: User, start, end) -> dict:
        """Received, distributed and discarded quantities per category (FR-37, AC-33).

        "Distributed" counts only hampers that were collected (FR-47), so
        packed-but-uncollected hampers are not included.
        """
        self.access.require(user, "report.read", "report:stock")
        start, end = _check_range(start, end)
        totals = defaultdict(lambda: {"received": 0.0, "distributed": 0.0,
                                      "discarded": 0.0, "returned": 0.0})
        units = {r.id: r.unit for r in self.repo.all("rules")}
        for m in self.repo.all("movements"):
            if start <= m.date <= end and m.kind != MovementKind.PACKED:
                row = totals[m.category]
                row[m.kind.value] = round(row[m.kind.value] + m.quantity, 3)
        rows = [{"category": c, "unit": units.get(c, ""), **v} for c, v in sorted(totals.items())]
        self.audit.record(user.id, "report", "report:stock", detail=f"{start}..{end}")
        return {"title": "Stock movement report", "start": start, "end": end, "rows": rows,
                "note": "" if rows else t("no_activity")}

    def demand(self, user: User, start, end) -> dict:
        """Households served, hampers distributed and unmet needs (FR-38, AC-34)."""
        self.access.require(user, "report.read", "report:demand")
        start, end = _check_range(start, end)
        households, hampers = set(), 0
        for c in self.repo.all("clients"):
            for v in c.visits:
                if start <= v.date <= end:
                    households.add(c.id)
                    hampers += 1
        unmet = defaultdict(lambda: {"requests": 0})
        for u in self.repo.all("unmet"):
            if start <= u.date <= end:
                unmet[(u.need, u.category)]["requests"] += 1
        unmet_rows = [{"need": need, "category": cat, **v} for (need, cat), v in sorted(unmet.items())]
        summary = {"households_served": len(households), "hampers_distributed": hampers,
                   "unmet_requests": sum(r["requests"] for r in unmet_rows)}
        self.audit.record(user.id, "report", "report:demand", detail=f"{start}..{end}")
        empty = not households and not unmet_rows
        # Only counts leave this method: no names, contact details or codes (FR-39)
        return {"title": "Demand report", "start": start, "end": end, "summary": summary,
                "rows": unmet_rows, "note": t("no_activity") if empty else ""}

    @staticmethod
    def to_csv(report: dict) -> str:
        """CSV text with a header row and the same figures as the screen (FR-40)."""
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([report["title"], f"{report['start']} to {report['end']}"])
        if "summary" in report:
            for key, value in report["summary"].items():
                writer.writerow([key.replace("_", " "), value])
        rows = report["rows"]
        if rows:
            writer.writerow(list(rows[0].keys()))
            for row in rows:
                writer.writerow(list(row.values()))
        elif report.get("note"):
            writer.writerow([report["note"]])
        return out.getvalue()
