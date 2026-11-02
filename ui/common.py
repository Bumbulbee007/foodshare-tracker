"""Shared helpers for every screen (presentation layer).

``attempt`` runs a service call and turns any expected error into a calm,
plain-English message, so the app never shows a crash screen (NFR-12).
"""

from __future__ import annotations

import logging

import streamlit as st

from foodshare.errors import FoodShareError, PermissionDeniedError
from foodshare.text import t

log = logging.getLogger("foodshare.ui")

GENERIC_ERROR = ("Something went wrong and nothing was saved. Please try again, "
                 "or ask a staff member for help.")


def attempt(fn, *args, **kwargs):
    """Call a service; show a friendly message and return None if it fails."""
    try:
        return fn(*args, **kwargs)
    except PermissionDeniedError:
        st.error(t("permission_denied"))
    except FoodShareError as exc:
        st.error(str(exc))
    except Exception:                       # unexpected bug: log it, never crash
        log.exception("Unexpected error in %s", getattr(fn, "__name__", fn))
        st.error(GENERIC_ERROR)
    return None


def succeeded(fn, *args, **kwargs) -> bool:
    """Like attempt, for calls that return nothing: True if no error happened."""
    marker = object()

    def wrapped():
        fn(*args, **kwargs)
        return marker
    return attempt(wrapped) is marker


def flash(message: str) -> None:
    """Keep a success message across the page refresh that follows a save."""
    st.session_state["flash"] = message
    st.rerun()


def show_flash() -> None:
    message = st.session_state.pop("flash", None)
    if message:
        st.success(message)


def table(rows: list[dict], empty: str = "Nothing to show yet.") -> None:
    if rows:
        st.dataframe(rows, hide_index=True)
    else:
        st.info(empty)


def system():
    return st.session_state["system"]


def user():
    return st.session_state["session"].user


ACCESSIBLE_CSS = """
<style>
/* NFR-03: text at least 14 pt (about 19 px) and large touch targets */
html, body, [class*="st-"], p, li, label, input, textarea, select, button {
    font-size: 19px !important;
}
h1 { font-size: 2.1rem !important; }
h2 { font-size: 1.6rem !important; }
h3 { font-size: 1.3rem !important; }
.stButton button, .stFormSubmitButton button, .stDownloadButton button {
    min-height: 3rem; padding: 0.4rem 1.2rem; font-weight: 600;
}
.tag {
    display: inline-block; margin: 0 0.4rem 0.4rem 0; padding: 0.25rem 0.8rem;
    border-radius: 1rem; background: #1F5F3F; color: #FFFFFF; font-weight: 600;
}
.big-code { font-size: 3rem !important; font-weight: 700; letter-spacing: 0.3rem; }
</style>
"""
