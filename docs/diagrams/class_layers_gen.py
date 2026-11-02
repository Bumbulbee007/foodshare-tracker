"""Generates class_layers.dot: application and data layers as a UML class diagram (Graphviz)."""
import html

CLASSES = {
    "FoodShareSystem": ([], ["settings() : dict", "daily_backup()", "audit_log(...) : list",
                             "list_backups() : list", "restore_backup(name)"]),
    "AuthService": ([], ["login(name, secret) : Session", "touch(session)", "is_expired(session) : bool"]),
    "UserService": ([], ["create_user(name, role, secret) : User", "set_active(name, active)", "list_users() : list"]),
    "StockService": ([], ["record_donation(lines, ...) : Donation", "record_offer(donor, items) : DonorOffer",
                          "stock_summary() : list", "expiring_soon(days) : list",
                          "low_stock_alerts() : list", "flag_batch(id, reason, note)"]),
    "ClientService": ([], ["create_client(data) : Client", "update_client(id, data) : Client",
                           "get_client(id) : Client", "add_note(id, text, restricted)",
                           "information_summary(id) : str", "delete_client(id)", "retention_review() : list"]),
    "HamperService": ([], ["save_rules(rules) : list", "book_pickup(client, date, slot) : Pickup",
                           "pickup_message(pickup) : dict", "generate_hamper(code) : Hamper",
                           "adjust_line(id, line, qty, reason)", "confirm_hamper(id, priority) : Task",
                           "confirm_collection(code) : dict", "return_uncollected(id)",
                           "safe_substitutes(hamper, line) : list"]),
    "TaskService": ([], ["create_task(kind, title, ...) : Task", "my_tasks() : list", "task_view(id) : dict",
                         "substitute_line(id, line, batch, note)", "mark_line_unavailable(id, line)",
                         "complete_task(id) : Task", "flag_issue(id, type, note)"]),
    "ReportService": ([], ["stock_movement(start, end) : dict", "demand(start, end) : dict", "to_csv(report) : str"]),
    "KioskService": ([], ["start(code, pin) : KioskSession", "choose(session, field, value)", "save(session) : str",
                          "is_expired(session) : bool"]),
    "AccessControl": (["PERMISSIONS : dict  (Table 22)"], ["allowed(permission) : bool", "require(permission, record)"]),
    "AuditService": ([], ["record(action, record, outcome, detail) : AuditEntry"]),
    "JsonRepository": (["- _store : dict", "+ settings : dict"], ["all(collection) : list", "get(collection, id)", "find(collection, **criteria) : list",
                        "next_id(collection) : str", "add(collection, entity)", "save(collection)",
                        "backup_if_needed(today)", "restore_backup(name)"]),
    "Entity": (["«abstract»"], ["to_dict() : dict", "from_dict(data) : Entity"]),
    "Clock": ([], ["now() : datetime", "today() : date"]),
    "FixedClock": ([], ["advance(**kwargs)", "set(moment)"]),
    "FoodShareError": ([], []),
    "ValidationError": (["+ field : str"], []),
    "PermissionDeniedError": ([], []), "AuthenticationError": ([], []),
    "NotFoundError": ([], []), "SessionExpiredError": ([], []),
}

def label(name):
    attrs, ops = CLASSES[name]
    stereo = ""
    if attrs and attrs[0].startswith("«"):
        stereo, attrs = attrs[0], attrs[1:]
    head = (f'<FONT POINT-SIZE="10">{html.escape(stereo)}</FONT><BR/>' if stereo else "") + f"<B>{name}</B>"
    rows = [f'<TR><TD BGCOLOR="#E8EEEA">{head}</TD></TR>']
    if attrs or ops:
        rows.append('<TR><TD ALIGN="LEFT" BALIGN="LEFT">' + ("<BR/>".join(html.escape(a) for a in attrs) or " ") + "</TD></TR>")
        rows.append('<TR><TD ALIGN="LEFT" BALIGN="LEFT">' + ("<BR/>".join("+ " + html.escape(o) for o in ops) or " ") + "</TD></TR>")
    return '<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4">' + "".join(rows) + "</TABLE>>"

out = ['digraph G {', 'graph [rankdir=TB, splines=spline, nodesep=0.25, ranksep=0.55, fontname="Helvetica", newrank=true];',
       'node [shape=plain, fontname="Helvetica", fontsize=11];', 'edge [fontname="Helvetica", fontsize=9, color="#555555"];']
for n in CLASSES:
    out.append(f'"{n}" [label={label(n)}];')
ranks = [["FoodShareSystem"], ["AuthService", "UserService", "StockService"], ["ClientService", "HamperService", "TaskService"],
         ["ReportService", "KioskService", "Clock"], ["AuditService", "AccessControl"],
         ["JsonRepository", "FixedClock", "FoodShareError"], ["Entity", "ValidationError", "PermissionDeniedError"],
         ["AuthenticationError", "NotFoundError", "SessionExpiredError"]]
for r in ranks:
    out.append("{rank=same; " + " ".join(f'"{x}";' for x in r) + "}")
# invisible edges force each group onto its own row, in order
for upper, lower in zip(ranks, ranks[1:]):
    for a in upper:
        for b in lower:
            out.append(f'"{a}" -> "{b}" [style=invis, weight=0];')
    break_ = None
for r in ranks:
    for a, b in zip(r, r[1:]):
        out.append(f'"{a}" -> "{b}" [style=invis, constraint=false];')
services = ["AuthService", "UserService", "StockService", "ClientService", "ReportService",
            "HamperService", "TaskService", "KioskService"]
# composition: diamond at the whole (FoodShareSystem)
for s in services + ["AccessControl", "AuditService", "JsonRepository"]:
    out.append(f'"FoodShareSystem" -> "{s}" [dir=back, arrowtail=diamond, color="#888888"];')
out.append('"FoodShareSystem" -> "Clock" [dir=back, arrowtail=odiamond, color="#888888", headlabel="1"];')
for s in ["StockService", "ClientService", "HamperService", "TaskService", "ReportService", "UserService"]:
    out.append(f'"{s}" -> "AccessControl" [style=dashed, arrowhead=vee, xlabel="require()", fontcolor="#555555"];')
for s in ["StockService", "ClientService", "HamperService", "TaskService", "AuditService", "KioskService"]:
    out.append(f'"{s}" -> "JsonRepository" [arrowhead=vee, color="#9aa7a0"];')
out.append('"TaskService" -> "HamperService" [arrowhead=vee, xlabel="safe substitutes"];')
out.append('"AccessControl" -> "AuditService" [arrowhead=vee, xlabel="logs denials"];')
out.append('"JsonRepository" -> "Entity" [dir=back, arrowtail=odiamond, headlabel="0..*", taillabel="1", xlabel="stores"];')
out.append('"Clock" -> "FixedClock" [dir=back, arrowtail=empty];')
for e in ["ValidationError", "PermissionDeniedError", "AuthenticationError", "NotFoundError", "SessionExpiredError"]:
    out.append(f'"FoodShareError" -> "{e}" [dir=back, arrowtail=empty];')
out.append("}")
open("class_layers.dot", "w").write("\n".join(out))
