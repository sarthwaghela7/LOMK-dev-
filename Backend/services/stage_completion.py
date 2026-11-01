"""Shared validation for the Calls-page Stage 2 completion gate."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


# Stage 2 is the required operational hand-off.  Stage 3/4 work and marking
# a call complete are deliberately gated on every one of these values.
STAGE2_REQUIRED_FIELDS = (
    "fd_m_self_action_item",
    "fd_m_manual_action_status",
    "fd_m_dependency",
    "fd_m_follow_up_dates",
)
# Blocker Comment is optional, and "# of Follow Up Attempts" is no longer a
# Calls-page column, so neither gates Stage 3/4 or completion. Their columns
# and the API fields remain for existing data and other pages.

STAGE2_FIELD_LABELS = {
    "fd_m_self_action_item": "Self Action Item",
    "fd_m_manual_action_status": "Action Status",
    "fd_m_dependency": "Dependency",
    "fd_m_follow_up_dates": "Follow Up Dates",
}

STAGE34_FIELDS = {
    "fd_m_case_status_change",
    "fd_m_date_entry",
    "fd_m_verification_comment",
    "fd_m_attachment",
    "fd_m_stage4_attachment",
    "fd_m_google_review",
}


def case_status_change_date():
    """Business date (Central time) a Case Status was selected -- system-generated."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Chicago")).date()


def _is_present(value: Any) -> bool:
    """Treat zero follow-up attempts as a supplied value, unlike blanks."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def missing_stage2_fields(record: Any, proposed: Mapping[str, Any] | None = None) -> list[str]:
    """Return human-readable required Stage 2 fields that are still blank.

    ``proposed`` supports a multi-field request which supplies the final Stage
    2 values and a Stage 3/4 value together, without incorrectly rejecting a
    valid atomic update.
    """
    proposed = proposed or {}
    return [
        STAGE2_FIELD_LABELS[field]
        for field in STAGE2_REQUIRED_FIELDS
        if not _is_present(proposed[field] if field in proposed else getattr(record, field, None))
    ]
