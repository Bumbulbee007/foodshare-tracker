"""Admin-only screens: accounts, allocation rules, audit log, backups and
retention review (FR-33, FR-36, FR-50, NFR-11, NFR-14)."""

from __future__ import annotations

from datetime import timedelta

import streamlit as st

from foodshare.models import Role

from .common import attempt, flash, show_flash, succeeded, system, table, user


def users_page():
    st.title("User accounts")
    show_flash()
    s, u = system(), user()
    users = attempt(s.users.list_users, u) or []
    table([{"Username": x.id, "Role": x.role.value, "Log-in with": "PIN" if x.uses_pin else "Password",
            "Active": "Yes" if x.active else "No"} for x in users])
    with st.form("new_user", clear_on_submit=True):
        st.markdown("**New account** - each person gets exactly one role")
        c1, c2, c3 = st.columns(3)
        name = c1.text_input("Username")
        role = c2.selectbox("Role", [r.value for r in Role])
        secret = c3.text_input("Password (8+ characters) or volunteer PIN (4 numbers)", type="password")
        if st.form_submit_button("Create account", type="primary"):
            if attempt(s.users.create_user, u, name, role, secret):
                flash(f"Account '{name.strip().lower()}' created.")
    others = [x for x in users if x.id != u.id]
    if others:
        with st.form("activate"):
            c1, c2 = st.columns(2)
            who = c1.selectbox("Account", [x.id for x in others])
            action = c2.radio("Change to", ["Deactivate", "Activate"], horizontal=True)
            if st.form_submit_button("Apply"):
                if attempt(s.users.set_active, u, who, action == "Activate"):
                    flash(f"{who} {'activated' if action == 'Activate' else 'deactivated'}.")


def rules_page():
    st.title("Allocation rules")
    show_flash()
    s, u = system(), user()
    st.caption("Quantity per person per visit for each food category. The same rules apply to every "
               "household. A rule set with a negative number or a missing category is not saved.")
    rules = attempt(s.hampers.list_rules, u) or []
    edited = st.data_editor([r.to_dict() for r in rules], num_rows="dynamic", hide_index=True,
                            column_config={"id": "Food category", "unit": "Unit", "per_adult": "Per adult",
                                           "per_child": "Per child", "per_infant": "Per infant"},
                            key="rules_editor")
    if st.button("Save rules", type="primary"):
        records = edited.to_dict("records") if hasattr(edited, "to_dict") else list(edited)
        if attempt(s.hampers.save_rules, u, records):
            flash("Allocation rules saved.")


def audit_page():
    st.title("Audit log")
    s, u = system(), user()
    c1, c2, c3, c4 = st.columns(4)
    who = c1.text_input("User")
    record = c2.text_input("Record contains (e.g. client:C-0001)")
    start = c3.date_input("From", value=s.clock.today() - timedelta(days=7))
    end = c4.date_input("To", value=s.clock.today())
    rows = attempt(s.audit_log, u, who.strip().lower(), record.strip(), start, end) or []
    table([{"When": e.timestamp.replace("T", " "), "User": e.user, "Action": e.action, "Record": e.record,
            "Outcome": e.outcome, "Detail": e.detail} for e in rows[:500]], "No matching entries.")


def system_page():
    st.title("Backups and privacy review")
    show_flash()
    s, u = system(), user()
    st.subheader("Daily backups")
    st.caption("A backup is made automatically on the first use of each day; the newest 14 are kept.")
    backups = attempt(s.list_backups, u) or []
    if backups:
        with st.form("restore"):
            name = st.selectbox("Backup", backups)
            sure = st.checkbox("I understand this replaces today's data with the backup")
            if st.form_submit_button("Restore backup"):
                if not sure:
                    st.error("Tick the box to confirm first.")
                elif succeeded(s.restore_backup, u, name):
                    flash(f"Data restored from {name}.")
    else:
        st.info("No backups yet.")
    st.subheader("Retention review")
    st.caption("Profiles with no visit in the last 24 months. Contact the household if possible, "
               "then delete their information from the Households page.")
    table(attempt(s.clients.retention_review, u) or [], "No profiles are due for review.")
