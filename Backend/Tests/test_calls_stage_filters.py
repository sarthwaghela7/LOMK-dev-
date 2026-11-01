import pytest
from fastapi import HTTPException

from routes import calls
from services.analysis_read_query import OPEN_CASE_TERMINAL_STATUSES


def test_open_cases_exclude_exactly_the_three_closing_statuses():
    # Stored spellings, lowercased: Open Cases = everything else, including blank.
    assert set(OPEN_CASE_TERMINAL_STATUSES) == {"dropped", "success ge", "success pc", "success - ge", "success - pc"}   # new + legacy spellings
    assert {"success ge", "success pc"} <= {s.lower() for s in calls.VALID_FD_CASE_STATUS_CHANGE}
    assert "dropped" in {s.lower() for s in calls.VALID_FD_CASE_STATUS_CHANGE}


def test_action_status_filter_accepts_only_existing_values():
    assert calls._validate_action_status_filter("Pending") == "Pending"
    assert calls._validate_action_status_filter("  ") is None
    assert calls._validate_action_status_filter(None) is None
    with pytest.raises(HTTPException) as exc:
        calls._validate_action_status_filter("Bogus")
    assert exc.value.status_code == 422


def test_pending_cases_status_list_is_not_overridden_by_the_stage_filter_list():
    # The older Pending Cases endpoint validates against VALID_ACTION_STATUSES;
    # the Stage 2 filter has its own constant and must not replace it.
    assert "Success - PC" in calls.VALID_ACTION_STATUSES and "Dropped" in calls.VALID_ACTION_STATUSES
    assert "Pending" not in calls.VALID_ACTION_STATUSES
    assert "Pending" in calls.STAGE2_ACTION_STATUSES
