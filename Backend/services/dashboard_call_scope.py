"""The canonical CDR population used by Dashboard call KPIs.

This module intentionally works on ORM calls as well as lightweight call-like
objects so the Calls APIs and the dashboard aggregate endpoint cannot drift.
"""

from __future__ import annotations


DASHBOARD_NUMBERS = frozenset({"3128781255", "2243374447", "2243939098"})
DASHBOARD_EXTENSIONS = frozenset({"101", "102", "201", "202", "203", "204", "205"})
DASHBOARD_SPECIAL_EXTENSION_MAPPINGS = {
    "601": "3128781255",
    "603": "2243374447",
    "605": "2243939098",
}


def normalize_dashboard_number(value: object) -> str:
    """Normalize 10/11-digit CDR routing values without altering storage."""
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def is_dashboard_call(call: object | None) -> bool:
    """Whether a CDR belongs in the explicitly configured Dashboard scope."""
    if call is None:
        return False
    src = normalize_dashboard_number(getattr(call, "src", None))
    dst = normalize_dashboard_number(getattr(call, "dst", None))
    extension = normalize_dashboard_number(getattr(call, "extension", None))

    if src in DASHBOARD_NUMBERS or dst in DASHBOARD_NUMBERS:
        return True
    if extension in DASHBOARD_EXTENSIONS:
        return True
    return (
        extension in DASHBOARD_SPECIAL_EXTENSION_MAPPINGS
        and (src == DASHBOARD_SPECIAL_EXTENSION_MAPPINGS[extension]
             or dst == DASHBOARD_SPECIAL_EXTENSION_MAPPINGS[extension])
    )
