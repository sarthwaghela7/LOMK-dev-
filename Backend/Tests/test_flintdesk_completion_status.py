"""A completed call must remain editable, and editing any completion-relevant
Stage 1 field (FD_AI_FIELDS -- Case Type, Urgency Flag, Call Outcome, etc.)
must invalidate case_closing_completed as part of that SAME update -- no
separate request, no full refetch. See routes/flintdesk/calls.py's
update_flintdesk_single_field (Calls page inline edits) and
update_flintdesk_manual_fields (Case Closing page / import).
"""
import contextlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from routes.flintdesk.calls import (
    FlintDeskManualFieldUpdateRequest,
    FlintDeskSingleFieldUpdateRequest,
    update_flintdesk_manual_fields,
    update_flintdesk_single_field,
)
from storage.models import CallAnalysisResult


@contextlib.contextmanager
def _wire(analysis: CallAnalysisResult):
    """Patch the call/analysis lookups both endpoints share so they operate
    on this in-memory analysis row without touching a real database.
    """
    call = SimpleNamespace(call_id=analysis.call_id, client=None, fd_processing_status=None)
    with patch("routes.flintdesk.calls._get_call_or_404", return_value=call), \
         patch("routes.flintdesk.calls._get_or_create_analysis_record", return_value=analysis), \
         patch("routes.flintdesk.calls.assert_manual_call_access"):
        yield


class _FakeQuery:
    """Just enough of the Query interface for the fast-path lookup in
    update_flintdesk_single_field: db.query(CallAnalysisResult).filter(...).first().
    """

    def __init__(self, result):
        self._result = result

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._result


class _FakeDB:
    def __init__(self, analysis=None):
        self._analysis = analysis

    def query(self, model):
        return _FakeQuery(self._analysis if model is CallAnalysisResult else None)

    def add(self, *_args, **_kwargs):
        pass

    def commit(self):
        pass

    def refresh(self, *_args, **_kwargs):
        pass

    def flush(self):
        pass


AGENT_USER = {"role": "agent", "fd_user_id": "agent-1", "name": "Priyanka"}


def _completed_analysis(**overrides):
    defaults = dict(
        id=1,
        call_id=str(uuid4()),
        case_closing_completed=True,
        case_closing_completed_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return CallAnalysisResult(**defaults)


def _stage2_complete_analysis(**overrides):
    defaults = dict(
        fd_m_self_action_item="Call client back",
        fd_m_action_due_date=datetime(2026, 9, 24).date(),
        fd_m_manual_action_status="Open",
        fd_m_dependency="None",
        fd_m_follow_up_dates="09-25-2026",
        fd_m_follow_up_attempts=0,
        fd_m_blocker_comment="No blocker",
    )
    defaults.update(overrides)
    return _completed_analysis(**defaults)


def test_fast_field_edit_clears_completion_when_case_type_changes():
    analysis = _completed_analysis(fd_a_case_type="Family")
    with _wire(analysis):
        response = update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_a_case_type", value="Divorce", expected_value="Family",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert analysis.fd_a_case_type == "Divorce"
    assert analysis.case_closing_completed is False
    assert analysis.case_closing_completed_at is None
    assert response["case_closing_completed"] is False
    assert response["case_closing_completed_at"] is None


@pytest.mark.parametrize(
    "field,value,expected_value",
    [
        ("fd_a_urgency_flag", "High", ""),
        ("fd_a_case_worker_assigned", "New Worker", ""),
        ("fd_a_call_outcome", "Connected - Redirected", ""),
        ("fd_a_call_summary", "Updated summary text.", ""),
    ],
)
def test_fast_field_edit_clears_completion_for_every_stage1_field(field, value, expected_value):
    analysis = _completed_analysis(**{field: expected_value or None})
    with _wire(analysis):
        update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field=field, value=value, expected_value=expected_value,
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert getattr(analysis, field) == value
    assert analysis.case_closing_completed is False


def test_fast_field_edit_leaves_completion_alone_for_non_completion_relevant_field():
    analysis = _stage2_complete_analysis(fd_m_blocker_comment="Old comment")
    with _wire(analysis):
        update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_m_blocker_comment", value="New comment", expected_value="Old comment",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert analysis.fd_m_blocker_comment == "New comment"
    assert analysis.case_closing_completed is True


def test_fast_field_edit_accepts_a_whitespace_only_legacy_follow_up_value_as_blank():
    # The Follow Up Dates popup parses whitespace-only text as no entries and
    # sends an empty expected value.  Historic imports could persist that
    # whitespace, so the stale-write guard must compare canonical values.
    analysis = _completed_analysis(fd_m_follow_up_dates="  \n  ")
    with _wire(analysis):
        response = update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_m_follow_up_dates",
                value="09-29-2026",
                expected_value="",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert analysis.fd_m_follow_up_dates == "09-29-2026"
    assert response["normalized_value"] == "09-29-2026"


def test_fast_field_edit_accepts_a_legacy_dash_follow_up_value_as_blank():
    # AF#9280 exposed this exact legacy state in production: the table treats
    # "-" as its empty-state placeholder, while the stale-write API used to
    # compare it literally with the empty expected value from the editor.
    analysis = _completed_analysis(fd_m_follow_up_dates="-")
    with _wire(analysis):
        response = update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_m_follow_up_dates",
                value="N/A",
                expected_value="",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert analysis.fd_m_follow_up_dates == "N/A"
    assert response["normalized_value"] == "N/A"


def test_fast_field_edit_still_rejects_a_meaningfully_stale_follow_up_value():
    analysis = _completed_analysis(fd_m_follow_up_dates="09-25-2026")
    with _wire(analysis), pytest.raises(HTTPException, match="updated by another user") as raised:
        update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_m_follow_up_dates",
                value="09-29-2026",
                expected_value="",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert raised.value.status_code == 409


def test_stage3_and_stage4_are_locked_until_stage2_is_complete():
    analysis = _completed_analysis(case_closing_completed=False, case_closing_completed_at=None)
    with _wire(analysis), pytest.raises(HTTPException, match="Complete all Stage 2 fields") as raised:
        update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_m_google_review", value="Requested", expected_value="",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert raised.value.status_code == 409


def test_stage4_is_editable_after_stage2_is_complete():
    analysis = _stage2_complete_analysis(case_closing_completed=False, case_closing_completed_at=None)
    with _wire(analysis):
        update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_m_google_review", value="Requested", expected_value="",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert analysis.fd_m_google_review == "Requested"


def test_stage3_is_editable_when_optional_action_due_date_is_blank():
    analysis = _stage2_complete_analysis(
        fd_m_action_due_date=None,
        case_closing_completed=False,
        case_closing_completed_at=None,
    )
    with _wire(analysis):
        update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_m_verification_comment", value="Verified", expected_value="",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert analysis.fd_m_verification_comment == "Verified"


def test_fast_field_edit_with_unchanged_value_does_not_clear_completion():
    analysis = _completed_analysis(fd_a_case_type="Family")
    with _wire(analysis):
        update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_a_case_type", value="Family", expected_value="Family",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert analysis.case_closing_completed is True


def test_manual_fields_endpoint_clears_completion_when_stage1_field_changes():
    analysis = _completed_analysis(fd_a_urgency_flag="Low")
    with _wire(analysis):
        response = update_flintdesk_manual_fields(
            call_id=str(analysis.call_id),
            payload=FlintDeskManualFieldUpdateRequest(fd_a_urgency_flag="High"),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert analysis.fd_a_urgency_flag == "High"
    assert analysis.case_closing_completed is False
    assert response["case_closing_completed"] is False


def test_manual_fields_endpoint_invalidates_once_for_multiple_field_edit():
    analysis = _completed_analysis(
        fd_a_case_type="Family", fd_a_call_outcome=None, fd_a_call_summary=None,
    )
    with _wire(analysis):
        response = update_flintdesk_manual_fields(
            call_id=str(analysis.call_id),
            payload=FlintDeskManualFieldUpdateRequest(
                fd_a_case_type="Divorce",
                fd_a_call_outcome="Connected - Resolved",
                fd_a_call_summary="Client called about a divorce filing.",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    # All three values persisted in the one call, and completion invalidated
    # exactly once (a single False, not toggled back and forth per field).
    assert analysis.fd_a_case_type == "Divorce"
    assert analysis.fd_a_call_outcome == "Connected - Resolved"
    assert analysis.fd_a_call_summary == "Client called about a divorce filing."
    assert analysis.case_closing_completed is False
    assert response["case_closing_completed"] is False


def test_manual_fields_endpoint_leaves_completion_alone_for_stage2_only_edit():
    analysis = _stage2_complete_analysis(fd_m_blocker_comment="Old")
    with _wire(analysis):
        response = update_flintdesk_manual_fields(
            call_id=str(analysis.call_id),
            payload=FlintDeskManualFieldUpdateRequest(fd_m_blocker_comment="New"),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert analysis.case_closing_completed is True
    assert response["case_closing_completed"] is True


def test_clearing_a_required_stage2_field_resets_completion():
    analysis = _stage2_complete_analysis(fd_m_self_action_item="Documented")
    with _wire(analysis):
        response = update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_m_self_action_item", value="", expected_value="Documented",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert analysis.fd_m_self_action_item is None
    assert analysis.case_closing_completed is False
    assert response["case_closing_completed"] is False


def test_clearing_optional_blocker_keeps_completion():
    analysis = _stage2_complete_analysis(fd_m_blocker_comment="Documented")
    with _wire(analysis):
        response = update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_m_blocker_comment", value="", expected_value="Documented",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert analysis.fd_m_blocker_comment is None
    assert analysis.case_closing_completed is True
    assert response["case_closing_completed"] is True


def test_case_status_change_sets_date_automatically_and_rejects_manual_date():
    from datetime import date
    analysis = _stage2_complete_analysis(fd_m_case_status_change="Open", fd_m_date_entry=date(2020, 1, 1))
    _FakeDB.execute = lambda self, *args, **kwargs: None  # status-history INSERT
    with _wire(analysis):
        update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_m_case_status_change", value="Same Client Context", expected_value="Open",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
        assert analysis.fd_m_case_status_change == "Same Client Context"
        assert analysis.fd_m_date_entry != date(2020, 1, 1)
        stamped = analysis.fd_m_date_entry
        update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_m_case_status_change", value="Initial Email Follow Up", expected_value="Same Client Context",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
        assert analysis.fd_m_case_status_change == "Initial Email Follow Up"
        assert analysis.fd_m_date_entry >= stamped
        with pytest.raises(HTTPException) as exc:
            update_flintdesk_single_field(
                call_id=str(analysis.call_id),
                payload=FlintDeskSingleFieldUpdateRequest(
                    field="fd_m_date_entry", value="01-01-2020", expected_value=None,
                ),
                user=AGENT_USER,
                db=_FakeDB(analysis),
            )
        assert exc.value.status_code == 422


def test_incomplete_call_stays_incomplete_after_stage1_edit():
    analysis = _completed_analysis(
        fd_a_case_type="Family", case_closing_completed=False, case_closing_completed_at=None,
    )
    with _wire(analysis):
        update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_a_case_type", value="Divorce", expected_value="Family",
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis),
        )
    assert analysis.case_closing_completed is False


def test_fast_field_edit_falls_back_to_call_lookup_when_no_analysis_row_yet():
    # update_flintdesk_single_field's hot path looks up CallAnalysisResult
    # directly by UUID call_id without a separate Call query (see its
    # performance comment). When no analysis row exists yet -- e.g. a call
    # that hasn't been through the AI pipeline -- it must still fall back to
    # _get_call_or_404 + _get_or_create_analysis_record to create one,
    # exactly as before this optimization.
    analysis = _completed_analysis(case_closing_completed=False, case_closing_completed_at=None)
    with _wire(analysis):
        response = update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(
                field="fd_a_case_type", value="Divorce", expected_value=None,
            ),
            user=AGENT_USER,
            db=_FakeDB(analysis=None),  # direct query finds nothing -- forces the fallback
        )
    assert analysis.fd_a_case_type == "Divorce"
    assert response["call_id"] == str(analysis.call_id)
