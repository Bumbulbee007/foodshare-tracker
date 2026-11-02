"""Volunteer screens: my tasks and packing (F-05, UC-07, UC-07a).

Volunteers see item lines and dietary tags only, never who the hamper is
for (FR-30). Big buttons, one task at a time, and a confirmation message
after every action support persona P3 (NFR-01, NFR-03).
"""

from __future__ import annotations

import streamlit as st

from foodshare.services.tasks import ISSUE_TYPES

from .common import attempt, flash, show_flash, succeeded, system, table, user
from .staff import flag_form

PRIORITY = {1: "High", 2: "Normal", 3: "Low"}


def my_tasks_page():
    st.title("My tasks")
    show_flash()
    s, u = system(), user()
    open_id = st.session_state.get("open_task")
    if open_id:
        task_detail(open_id)
        return

    tasks = attempt(s.tasks.my_tasks, u) or []
    if not tasks:
        st.info("No tasks right now. Thank you! Ask a staff member if you would like something to do.")
        return
    st.caption("Your tasks first, then open tasks anyone can take. Highest priority at the top.")
    for task in tasks:
        with st.container(border=True):
            mine = task.assigned_to == u.id
            st.markdown(f"**{task.title}**")
            st.caption(f"{task.kind.value} | Priority: {PRIORITY[task.priority]} | "
                       f"{'Assigned to you' if mine else 'Open - anyone can take it'}")
            if mine:
                if st.button("Open task", key=f"open_{task.id}", type="primary"):
                    st.session_state["open_task"] = task.id
                    st.rerun()
            elif st.button("Take this task", key=f"claim_{task.id}"):
                if attempt(s.tasks.claim_task, u, task.id):
                    st.session_state["open_task"] = task.id
                    flash("The task is now yours.")


def task_detail(task_id: str):
    s, u = system(), user()
    view = attempt(s.tasks.task_view, u, task_id)
    if st.button("Back to my tasks"):
        st.session_state.pop("open_task", None)
        st.session_state.pop("substitute_line", None)
        st.rerun()
    if view is None:
        st.session_state.pop("open_task", None)
        return
    st.subheader(view["title"])
    if view["tags"]:
        st.markdown("This hamper must be: " + " ".join(f"<span class='tag'>{t}</span>" for t in view["tags"]),
                    unsafe_allow_html=True)

    packing = view["kind"] == "Pack hamper" and view.get("hamper_status") == "confirmed"
    if packing:
        st.markdown("**Tick each item as you pack it.**")
        for line in view["lines"]:
            cols = st.columns([5, 2])
            label = f"{line['item']} - {line['quantity']:g} {line['unit']}"
            if line["substituted_from"]:
                label += f"  (instead of {line['substituted_from']})"
            ticked = cols[0].checkbox(label, value=line["packed"], key=f"pack_{task_id}_{line['index']}")
            if ticked != line["packed"]:
                if succeeded(s.tasks.set_line_packed, u, task_id, line["index"], ticked):
                    st.rerun()
            if not line["packed"] and cols[1].button("Not on the shelf?", key=f"sub_{task_id}_{line['index']}"):
                st.session_state["substitute_line"] = line["index"]
                st.rerun()
        if view["shortfalls"]:
            st.caption("Not available this week: " +
                       ", ".join(f"{g['category']} ({g['quantity']:g} {g['unit']})" for g in view["shortfalls"]))
        index = st.session_state.get("substitute_line")
        if index is not None and index < len(view["lines"]):
            substitute_panel(task_id, view["lines"][index])

    with st.expander("Report a problem with this task"):
        with st.form(f"issue_{task_id}", clear_on_submit=True):
            issue = st.radio("What is the problem?", ISSUE_TYPES, index=None)
            note = st.text_input("Short note (optional)")
            if st.form_submit_button("Send to staff"):
                if attempt(s.tasks.flag_issue, u, task_id, issue or "", note):
                    flash("Thank you. Staff can now see this problem.")

    if st.button("Mark task complete", type="primary"):
        if attempt(s.tasks.complete_task, u, task_id):
            st.session_state.pop("open_task", None)
            st.session_state.pop("substitute_line", None)
            flash("Well done - the task is complete and stock has been updated.")


def substitute_panel(task_id: str, line: dict):
    """Safe substitutes chosen by the system, not the packer (FR-49, UC-07a)."""
    s, u = system(), user()
    st.markdown(f"#### Replacing {line['item']}")
    options = attempt(s.tasks.substitutes_for, u, task_id, line["index"]) or []
    if options:
        labels = {f"{o['item']} (use by {o['expiry']})": o["batch_id"] for o in options}
        with st.form(f"substitute_{task_id}"):
            st.caption("Only items that are safe for this household are listed.")
            choice = st.radio("Use instead", list(labels))
            note = st.text_input("Note for the household",
                                 value=f"{choice.split(' (')[0]} replaced {line['item'].lower()} this week "
                                       f"as {line['item'].lower()} is out of stock.")
            if st.form_submit_button("Use this substitute", type="primary"):
                if succeeded(s.tasks.substitute_line, u, task_id, line["index"], labels[choice], note):
                    st.session_state.pop("substitute_line", None)
                    flash("Substitute added. Please pack it and tick it.")
    else:
        st.warning("There is no safe substitute in stock. Staff will record this as a need we could not meet.")
        if st.button("Leave this item out"):
            if succeeded(s.tasks.mark_line_unavailable, u, task_id, line["index"]):
                st.session_state.pop("substitute_line", None)
                flash("Item left out and recorded.")
    if st.button("Cancel"):
        st.session_state.pop("substitute_line", None)
        st.rerun()


def volunteer_flag_page():
    st.title("Flag a stock problem")
    show_flash()
    st.caption("Use this for damaged, expired or missing items on the shelves.")
    flag_form(prefix="volunteer")
