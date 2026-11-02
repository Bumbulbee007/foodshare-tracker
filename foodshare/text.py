"""Interface text and respectful-language checks (NFR-04, NFR-05, AD-07).

All user-facing wording lives in ``text/en.json`` so it can be reviewed in
one place and translated later (F-12). The banned-terms list keeps
stigmatising words out of screens, notes and messages.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

TEXT_DIR = Path(__file__).parent / "text"


@lru_cache(maxsize=None)
def load_text() -> dict:
    with (TEXT_DIR / "en.json").open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=None)
def banned_terms() -> tuple[str, ...]:
    with (TEXT_DIR / "banned_terms.json").open(encoding="utf-8") as fh:
        return tuple(json.load(fh))


def t(key: str, **values) -> str:
    """Look up a message by key, e.g. t("code_not_found")."""
    text = load_text()
    for part in key.split("."):
        text = text[part]
    return text.format(**values) if values else text


def find_banned_terms(text: str) -> list[str]:
    """Return every banned term found in ``text`` (whole words, any case)."""
    found = []
    for term in banned_terms():
        if re.search(r"\b" + re.escape(term) + r"\b", text, flags=re.IGNORECASE):
            found.append(term)
    return found
