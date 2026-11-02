"""Client self-service kiosk (F-08, UC-11).

One question per screen, icons beside each option, plain words (FR-42),
and back to the start after 60 seconds without input (FR-43).
"""

from __future__ import annotations

import streamlit as st

from foodshare.errors import FoodShareError, SessionExpiredError
from foodshare.text import t

ICONS = {"SMS": "💬", "Phone call": "📞", "Email": "✉️", "In person": "🙂"}


def _reset(message: str | None = None):
    for key in ("kiosk_session", "kiosk_step", "kiosk_code"):
        st.session_state.pop(key, None)
    if message:
        st.session_state["kiosk_message"] = message
    st.rerun()


def kiosk_page(system):
    st.title("Update my pickup")
    message = st.session_state.pop("kiosk_message", None)
    if message:
        st.info(message)
    step = st.session_state.get("kiosk_step", "start")
    session = st.session_state.get("kiosk_session")

    if session is not None:
        try:
            system.kiosk.touch(session)
        except SessionExpiredError as exc:
            _reset(str(exc))

    if step == "start":
        st.write(t("kiosk.welcome"))
        if st.button("▶  Start", type="primary"):
            st.session_state["kiosk_step"] = "code"
            st.rerun()

    elif step == "code":
        with st.form("kiosk_code_form"):
            code = st.text_input(t("kiosk.enter_code"), max_chars=3)
            if st.form_submit_button("Next ▶", type="primary"):
                st.session_state["kiosk_code"] = code
                st.session_state["kiosk_step"] = "pin"
                st.rerun()

    elif step == "pin":
        with st.form("kiosk_pin_form"):
            pin = st.text_input(t("kiosk.enter_pin"), type="password", max_chars=4)
            if st.form_submit_button("Next ▶", type="primary"):
                try:
                    st.session_state["kiosk_session"] = system.kiosk.start(st.session_state.get("kiosk_code", ""), pin)
                    st.session_state["kiosk_step"] = "choose"
                    st.rerun()
                except FoodShareError as exc:
                    _reset(str(exc))

    elif step == "choose":
        choices = system.kiosk.current_choices(session)
        st.write(f"Your pickup: **{choices['date']}, {choices['slot']}**. We contact you by **{choices['contact_method']}**.")
        st.write(t("kiosk.choose"))
        c1, c2 = st.columns(2)
        if c1.button("🕓  Pickup time", type="primary"):
            st.session_state["kiosk_step"] = "slot"
            st.rerun()
        if c2.button("💬  How we contact you", type="primary"):
            st.session_state["kiosk_step"] = "contact"
            st.rerun()

    elif step in ("slot", "contact"):
        choices = system.kiosk.current_choices(session)
        field = "slot" if step == "slot" else "contact_method"
        options = choices["slots"] if step == "slot" else choices["contact_methods"]
        with st.form(f"kiosk_{step}_form"):
            label = "Choose a pickup time" if step == "slot" else "How should we contact you?"
            value = st.radio(label, options,
                             format_func=lambda o: f"🕓  {o}" if step == "slot" else f"{ICONS.get(o, '')}  {o}")
            if st.form_submit_button("Save ✔", type="primary"):
                try:
                    system.kiosk.choose(session, field, value)
                    _reset(system.kiosk.save(session))
                except SessionExpiredError as exc:
                    _reset(str(exc))
                except FoodShareError as exc:
                    _reset(str(exc))

    if step != "start" and st.button("✖  Finish"):
        _reset()
