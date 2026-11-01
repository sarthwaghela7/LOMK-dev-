from storage.models import Call, CallAnalysisResult
import pytest
from datetime import date
from types import SimpleNamespace

from fastapi import HTTPException
from routes.flintdesk.calls import (
    FlintDeskManualFieldUpdateRequest,
    _assert_calls_page_edit_access,
    _display_fast_field_value,
    _normalize_fast_field_value,
    _serialize_client,
)


def test_flintdesk_fields_exist_on_models():
    call_fields = {col.name for col in Call.__table__.columns}
    analysis_fields = {col.name for col in CallAnalysisResult.__table__.columns}

    assert "fd_processing_status" in call_fields

    for name in [
        "fd_a_client_name",
        "fd_a_client_type",
        "fd_a_lead_source",
        "fd_a_email_address",
        "fd_a_urgency_flag",
        "fd_a_case_type",
        "fd_a_case_worker_assigned",
        "fd_a_call_outcome",
        "fd_a_call_summary",
        "fd_m_case_status_change",
        "fd_m_date_entry",
        "fd_m_verification_comment",
        "fd_m_attachment",
        "fd_m_google_review",
    ]:
        assert name in analysis_fields


def test_stage_1_ai_fields_are_accepted_for_agent_correction():
    payload = FlintDeskManualFieldUpdateRequest(
        fd_a_email_address="testing@gmail.com",
        fd_a_call_summary="Agent-entered correction.",
    )

    supplied = (
        payload.model_fields_set
        if hasattr(payload, "model_fields_set")
        else payload.__fields_set__
    )
    assert payload.fd_a_email_address == "testing@gmail.com"
    assert {"fd_a_email_address", "fd_a_call_summary"}.issubset(supplied)


def test_every_authenticated_agent_can_edit_calls_page_action_fields():
    # Calls-page fields are not constrained to the agent's extension
    # assignment. Authentication is still required.
    _assert_calls_page_edit_access({"role": "agent", "fd_user_id": "agent-id"})


def test_action_due_date_uses_date_only_mm_dd_yyyy():
    saved = _normalize_fast_field_value("fd_m_action_due_date", "09-15-2026")
    assert saved.isoformat() == "2026-09-15"
    assert _display_fast_field_value("fd_m_action_due_date", saved) == "09-15-2026"


def test_action_due_date_also_accepts_excel_style_slashes():
    # Excel/Sheets commonly copies dates with slashes rather than dashes --
    # a straight paste into /calls/import shouldn't fail on formatting alone.
    saved = _normalize_fast_field_value("fd_m_action_due_date", "09/15/2026")
    assert saved.isoformat() == "2026-09-15"


@pytest.mark.parametrize("value", ["02-30-2026", "15-09-2026", "September 15, 2026"])
def test_action_due_date_rejects_invalid_or_ambiguous_values(value):
    with pytest.raises(HTTPException) as exc:
        _normalize_fast_field_value("fd_m_action_due_date", value)
    assert exc.value.status_code == 422


def test_client_profile_email_is_returned_when_name_is_blank():
    client = SimpleNamespace(
        client_id="CL-1",
        phone_number="1234567890",
        first_call_date=date(2026, 9, 1),
        last_call_date=date(2026, 9, 1),
        total_calls=1,
        client_status="Open",
        client_name=None,
        client_email="saved@example.com",
    )
    assert _serialize_client(client)["client_email"] == "saved@example.com"
