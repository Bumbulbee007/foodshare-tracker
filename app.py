"""FoodShare Tracker - Streamlit entry point (presentation layer).

Run with:   streamlit run app.py

This file handles log-in, the role-based menu and the session time-out.
Each screen lives in the ``ui`` package. Screens talk only to the
FoodShareSystem services; they never read or write data files (NFR-18).
"""

from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

from foodshare.errors import AuthenticationError, SessionExpiredError
from foodshare.models import Role
from foodshare.seed import seed_demo
from foodshare.system import FoodShareSystem
from foodshare.text import t
from ui import admin, kiosk, staff, volunteer
from ui.common import ACCESSIBLE_CSS

DATA_DIR = Path(__file__).resolve().parent / "data"
logging.basicConfig(filename=str(Path(__file__).resolve().parent / "foodshare.log"),
                    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

st.set_page_config(page_title="FoodShare Tracker", page_icon="🥫", layout="wide")
st.markdown(ACCESSIBLE_CSS, unsafe_allow_html=True)


@st.cache_resource
def get_system() -> FoodShareSystem:
    """One system per running app. Demo data is created on the very first run."""
    if not (DATA_DIR / "users.json").exists():
        return seed_demo(DATA_DIR)
    return FoodShareSystem(DATA_DIR)


# Menu for each role: label -> screen function (SRS Table 22)
STAFF_MENU = {
    "Today": staff.dashboard_page,
    "Pickup counter": staff.counter_page,
    "Hampers": staff.hampers_page,
    "Households": staff.households_page,
    "Donations": staff.donations_page,
    "Stock": staff.stock_page,
    "Volunteer tasks": staff.tasks_page,
    "Reports": staff.reports_page,
}
ADMIN_EXTRA = {
    "User accounts": admin.users_page,
    "Allocation rules": admin.rules_page,
    "Audit log": admin.audit_page,
    "Backups and privacy review": admin.system_page,
}
VOLUNTEER_MENU = {
    "My tasks": volunteer.my_tasks_page,
    "Flag a stock problem": volunteer.volunteer_flag_page,
}


def menu_for(role: Role) -> dict:
    if role == Role.VOLUNTEER:
        return VOLUNTEER_MENU
    if role == Role.ADMIN:
        return {**STAFF_MENU, **ADMIN_EXTRA}
    return STAFF_MENU


def log_out(message: str | None = None) -> None:
    system = st.session_state.get("system")
    keep = {"system": system}
    st.session_state.clear()
    st.session_state.update(keep)
    if message:
        st.session_state["login_message"] = message
    st.rerun()


def login_screen(system: FoodShareSystem) -> None:
    st.title("FoodShare Tracker")
    message = st.session_state.pop("login_message", None)
    if message:
        st.warning(message)
    left, right = st.columns([3, 2])
    with left:
        with st.form("login"):
            username = st.text_input("Username")
            secret = st.text_input("Password or volunteer PIN", type="password")
            if st.form_submit_button("Log in", type="primary"):
                try:
                    st.session_state["session"] = system.auth.login(username, secret)
                    st.rerun()
                except AuthenticationError as exc:
                    st.error(str(exc))
    with right:
        st.markdown("**Are you collecting food?**")
        st.write("You can change your pickup time or how we contact you on the kiosk.")
        if st.button("Open the client kiosk"):
            st.session_state["mode"] = "kiosk"
            st.rerun()


def idle_watch(system: FoodShareSystem) -> None:
    """Checks every few seconds whether the screen has been left idle.

    Staff sessions end after 10 minutes (NFR-09) and the kiosk returns to
    its start screen after 60 seconds (FR-43), even if nobody touches it.
    """
    if not hasattr(st, "fragment"):
        return

    @st.fragment(run_every=5)
    def _check():
        session = st.session_state.get("session")
        kiosk_session = st.session_state.get("kiosk_session")
        if session is not None and system.auth.is_expired(session):
            st.session_state.clear()
            st.session_state["login_message"] = t("session_expired")
            st.rerun(scope="app")
        if kiosk_session is not None and system.kiosk.is_expired(kiosk_session):
            for key in ("kiosk_session", "kiosk_step", "kiosk_code"):
                st.session_state.pop(key, None)
            st.session_state["kiosk_message"] = t("kiosk.timeout")
            st.rerun(scope="app")

    _check()


def main() -> None:
    system = get_system()
    system.daily_backup()
    st.session_state["system"] = system
    idle_watch(system)

    if st.session_state.get("mode") == "kiosk":
        kiosk.kiosk_page(system)
        if st.sidebar.button("Staff: leave kiosk mode"):
            st.session_state.pop("mode", None)
            st.rerun()
        return

    session = st.session_state.get("session")
    if session is None:
        login_screen(system)
        return
    try:
        system.auth.touch(session)                     # NFR-09
    except SessionExpiredError as exc:
        log_out(str(exc))

    user = session.user
    menu = menu_for(user.role)
    with st.sidebar:
        st.markdown(f"### FoodShare Tracker\nLogged in as **{user.id}** ({user.role.value})")
        page = st.radio("Go to", list(menu), label_visibility="collapsed")
        if st.button("Log out"):
            log_out("You have logged out.")
    menu[page]()


main()
