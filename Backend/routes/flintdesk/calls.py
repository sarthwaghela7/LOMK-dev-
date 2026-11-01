from __future__ import annotations

import os
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import or_, text

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from services.Database_sync.f3 import F3Processor
from services.analysis_jobs import AnalysisBusy, AnalysisNeedsReview
from middleware.auth import get_current_user, require_fd_team_lead
from storage.db import get_db
from storage.models import Call, CallAnalysisResult, Client, FDUser
from storage.supabase_storage import SupabaseStorageService
from services.call_access import assert_manual_call_access, is_inbound_call
from services.client_lifecycle import resolve_client_for_call
from services import case_closure
from services.stage_completion import STAGE2_REQUIRED_FIELDS, STAGE34_FIELDS, case_status_change_date, missing_stage2_fields
from utils.config import MAX_ATTACHMENT_SIZE_MB
from routes.flintdesk.tasks import parse_task_date
from routes.flintdesk.leads import _phone

router = APIRouter(prefix="/api/flintdesk", tags=["FlintDesk"])
logger = logging.getLogger(__name__)

FD_AI_FIELDS = [
    "fd_a_client_name",
    "fd_a_client_type",
    "fd_a_lead_source",
    "fd_a_email_address",
    "fd_a_urgency_flag",
    "fd_a_case_type",
    "fd_a_case_worker_assigned",
    "fd_a_call_outcome",
    "fd_a_call_summary",
]

FD_MANUAL_FIELDS = [
    "fd_m_client_name",
    "fd_m_self_action_item",
    "fd_m_action_due_date",
    "fd_m_action_due_note",
    "fd_m_manual_action_status",
    "fd_m_dependency",
    "fd_m_follow_up_dates",
    "fd_m_follow_up_attempts",
    "fd_m_blocker_comment",
    "fd_m_case_status_change",
    "fd_m_date_entry",
    "fd_m_verification_comment",
    "fd_m_attachment",
    "fd_m_stage4_attachment",
    "fd_m_google_review",
]

# Calls-page edits are intentionally limited to these persisted analysis
# columns.  Keeping the list server-side prevents a client from using the
# fast endpoint to mutate unrelated analysis data.
FAST_EDITABLE_FIELDS = set(FD_AI_FIELDS + FD_MANUAL_FIELDS + ["front_desk_person_name"])

# Call-table fields whose displayed value comes from the client profile
# (routes.calls.serialize_analysis_rows), mapped to the profile attribute.
PROFILE_BACKED_FIELDS = {
    "fd_a_client_name": "client_name",
    "fd_m_client_name": "client_name",
    "fd_a_email_address": "client_email",
}
DATE_ONLY_FIELDS = {"fd_m_action_due_date", "fd_m_date_entry"}

# All authenticated FlintDesk agents may complete or correct the permitted
# Calls-page fields after reviewing a call.  This includes Stage 1 values and
# manual action fields such as Self Action Item and Action Due Date.  The
# allow-list above still prevents an agent from changing unrelated columns;
# extension assignment checks remain in place for routes outside the Calls
# page (for example attachment operations).
CALLS_PAGE_AGENT_EDITABLE_FIELDS = FAST_EDITABLE_FIELDS | {"client_status"}

from services.case_lifecycle import CASE_STATUS_OPTIONS, accepted_status
VALID_FD_CASE_STATUS_CHANGE = set(CASE_STATUS_OPTIONS)

VALID_CLIENT_STATUSES = {"Open", "Pending", "Closed", "Reopened"}

# Attachments are optional screenshots/images only (not required fields).
ALLOWED_ATTACHMENT_EXTENSIONS = {".png", ".jpg", ".jpeg"}
ALLOWED_ATTACHMENT_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg"}
MAX_ATTACHMENT_SIZE_BYTES = int(MAX_ATTACHMENT_SIZE_MB * 1024 * 1024)


class FlintDeskManualFieldUpdateRequest(BaseModel):
    # Stage 1 starts as LLM output, but an assigned agent may correct or
    # complete it when the transcript is ambiguous or the model missed data.
    fd_a_client_name: Optional[str] = None
    fd_a_client_type: Optional[str] = None
    fd_a_lead_source: Optional[str] = None
    fd_a_email_address: Optional[str] = None
    fd_a_urgency_flag: Optional[str] = None
    fd_a_case_type: Optional[str] = None
    fd_a_case_worker_assigned: Optional[str] = None
    fd_a_call_outcome: Optional[str] = None
    fd_a_call_summary: Optional[str] = None
    fd_m_client_name: Optional[str] = None
    fd_m_self_action_item: Optional[str] = None
    fd_m_action_due_date: Optional[date] = None
    fd_m_action_due_note: Optional[str] = None
    fd_m_manual_action_status: Optional[str] = None
    fd_m_dependency: Optional[str] = None
    fd_m_follow_up_dates: Optional[str] = None
    fd_m_follow_up_attempts: Optional[int] = None
    fd_m_blocker_comment: Optional[str] = None
    fd_m_case_status_change: Optional[str] = None
    fd_m_date_entry: Optional[date] = None
    fd_m_verification_comment: Optional[str] = None
    fd_m_attachment: Optional[str] = None
    fd_m_stage4_attachment: Optional[str] = None
    fd_m_google_review: Optional[str] = None
    front_desk_person_name: Optional[str] = None
    client_status: Optional[str] = None

    # Reject unknown fields so edits cannot silently target the wrong column.
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class FlintDeskSingleFieldUpdateRequest(BaseModel):
    """Small, conflict-aware request used by the Calls page's inline editor."""

    field: str
    value: Any = None
    expected_value: Any = None

    model_config = ConfigDict(extra="forbid")


def _parse_calls_date(value: Any, field: str) -> date | None:
    """Parse an unambiguous date-only Calls-page value without time zones."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"{field} must use MM-DD-YYYY.")
    # Excel/Sheets commonly copies dates with slashes (e.g. "09/19/2026")
    # rather than the dashes this page's own date inputs use -- accept both
    # so a straight paste from a spreadsheet doesn't fail on formatting
    # alone. See the "Date" import-verification column in /calls/import.
    # A date copied back out of the Calls Excel export is commonly rendered
    # by Sheets as e.g. ``09/22/26 12:00 AM``.  Accept that export format as
    # well as the date-only formats so export -> paste -> import is safe.
    for format_string in (
        "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d",
        "%m/%d/%y %I:%M %p", "%m/%d/%Y %I:%M %p",
    ):
        try:
            return datetime.strptime(value.strip(), format_string).date()
        except ValueError:
            continue
    raise HTTPException(status_code=422, detail=f"{field} must be a valid date in MM-DD-YYYY format.")


def _normalize_fast_field_value(field: str, value: Any) -> Any:
    if field in DATE_ONLY_FIELDS:
        return _parse_calls_date(value, field)
    if field == "fd_m_follow_up_attempts":
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"{field} must be a whole number.") from exc
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    normalized = value.strip()
    # The Calls table has always used a dash as its visual empty-state
    # placeholder.  Some historic imports also persisted that placeholder in
    # manual text columns.  The editor opens a dash as an empty value, so it
    # must have the same canonical meaning on the API side or the compare-
    # and-set guard will reject that record forever as falsely stale.
    return None if normalized in {"", "-"} else normalized


def _display_fast_field_value(field: str, value: Any) -> Any:
    if field in DATE_ONLY_FIELDS and isinstance(value, date):
        return value.strftime("%m-%d-%Y")
    return value


def _get_call_or_404(call_id: str, db: Session) -> Call:
    """Lookup a call by UUID first, then by call_reference for human-friendly IDs.

    This prevents invalid values like AF#147 from reaching the DB as if they were
    UUIDs, which previously caused a 500 from the SQLAlchemy/UUID comparison layer.
    """
    raw = str(call_id).strip()
    try:
        parsed_uuid = uuid.UUID(raw)
        call = db.query(Call).filter(Call.call_id == parsed_uuid).first()
    except (ValueError, TypeError):
        # Fix: values like AF#147 are not valid UUIDs; they are stored in the
        # call_reference field, so fall back to matching that column instead of
        # letting the invalid cast blow up as a server error. A bare number
        # (e.g. "147", as shown in the Calls page's own "Call ID" column) is
        # the AF# number on its own -- accept it directly as a shorthand for
        # "AF#147" rather than requiring the prefix to be retyped by hand.
        reference = f"AF#{raw}" if raw.isdigit() else raw
        call = db.query(Call).filter(Call.call_reference == reference).first()

    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return call


def _assert_call_access(call: Call, user: dict, db: Session):
    assert_manual_call_access(call, user, db)


def _assert_calls_page_edit_access(user: dict) -> None:
    """Calls-page editing is available to every authenticated FD user."""
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")


# Roles that may still correct/reassign the Agent cell on someone else's
# behalf. Matches the privileged-role set used elsewhere for FlintDesk
# review/config authority (see middleware.auth.require_fd_dependency_reviewer).
_PRIVILEGED_CALLS_ROLES = {"admin", "team_lead", "subject_matter_expert", "superuser", "super_user"}


def _assert_agent_self_identification(field: str, normalized_value, user: dict, *, call=None, db=None) -> None:
    """Inbound ownership is shared; retain self-identification elsewhere."""
    if field != "front_desk_person_name" or not normalized_value:
        return
    role = str(user.get("role", "")).strip().lower().replace(" ", "_")
    if role in _PRIVILEGED_CALLS_ROLES:
        return
    own_name = str(user.get("name", "")).strip().casefold()
    if own_name and str(normalized_value).strip().casefold() != own_name:
        if call is not None and db is not None and is_inbound_call(call, db):
            return
        raise HTTPException(
            status_code=403,
            detail="You can only enter your own name, not another agent's name.",
        )


def _get_or_create_analysis_record(call: Call, db: Session) -> CallAnalysisResult:
    analysis = (
        db.query(CallAnalysisResult)
        .filter(CallAnalysisResult.call_id == str(call.call_id))
        .first()
    )
    if analysis:
        return analysis

    analysis = CallAnalysisResult(
        call_id=str(call.call_id),
        call_number=call.call_number,
        call_reference=call.call_reference,
        call_recording_date=str(call.call_date) if call.call_date else None,
        call_duration=str(call.billsec) if call.billsec else None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(analysis)
    return analysis


def _coerce_analysis_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key in FD_AI_FIELDS:
        value = payload.get(key, None)
        if value is None:
            cleaned[key] = None
        elif isinstance(value, str):
            cleaned_value = value.strip()
            cleaned[key] = cleaned_value or None
        elif isinstance(value, (dict, list)):
            cleaned[key] = value
        else:
            cleaned[key] = str(value)
    return cleaned


def _record_status_change(db: Session, analysis: CallAnalysisResult, previous: str | None, current: str, user: dict[str, Any] | None):
    if (previous or "").strip() == current.strip():
        return
    db.execute(text("""
        INSERT INTO fd_case_status_history (analysis_id, call_id, previous_status, new_status, changed_by)
        VALUES (:analysis_id, :call_id, :previous_status, :new_status, :changed_by)
    """), {
        "analysis_id": analysis.id,
        "call_id": analysis.call_id,
        "previous_status": previous,
        "new_status": current,
        "changed_by": user.get("fd_user_id") if user else None,
    })


@router.get("/calls/{call_id}")
def get_flintdesk_call(call_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """FlintDesk-specific call state read route."""
    call = _get_call_or_404(call_id, db)
    _assert_call_access(call, user, db)
    analysis = _get_or_create_analysis_record(call, db)

    return {
        "call_id": str(call.call_id),
        "client": _serialize_client(call.client, db),
        "client_call_history": _client_call_history(call.client_id, db),
        "fd_processing_status": call.fd_processing_status or "not processed",
        "analysis": {key: getattr(analysis, key) for key in FD_AI_FIELDS},
        "manual": {key: getattr(analysis, key) for key in FD_MANUAL_FIELDS},
    }


def _serialize_client(client: Client | None, db: Session | None = None):
    if not client:
        return None
    payload = {
        "client_id": client.client_id,
        "phone_number": client.phone_number,
        "first_call_date": client.first_call_date.isoformat(),
        "last_call_date": client.last_call_date.isoformat(),
        "total_calls": client.total_calls,
        "client_status": client.client_status,
        "client_email": str(client.client_email or "").strip(),
    }
    if db:
        # The modal must use the same de-duplicated call population as its
        # history list, never a stale counter from an older duplicate CDR.
        payload["total_calls"] = len(_client_call_history(client.client_id, db))
    # Client identity is stored with the associated call analysis, not in the
    # canonical phone-based clients table. Read it only from that client's
    # calls and never substitute an assigned front-desk agent name.
    if str(client.client_name or "").strip():
        payload["client_name"] = client.client_name
    elif db:
        agent_names = {
            str(name or "").strip().casefold()
            for (name,) in db.query(FDUser.name).all()
            if str(name or "").strip()
        }
        client_call_ids = [
            str(call_id)
            for (call_id,) in db.query(Call.call_id)
            .filter(Call.client_id == client.client_id)
            .all()
        ]
        analysis = (
            db.query(CallAnalysisResult)
            .filter(CallAnalysisResult.call_id.in_(client_call_ids))
            .order_by(CallAnalysisResult.call_recording_date_normalized.desc())
            .first()
            if client_call_ids
            else None
        )
        if analysis:
            name = str(analysis.fd_a_client_name or analysis.client_name or "").strip()
            payload["client_name"] = name if name.casefold() not in agent_names else ""
            # A profile email is canonical even when the profile name has not
            # been entered yet.  Do not hide it behind an older per-call AI
            # value merely because client_name is blank.
            payload["client_email"] = str(
                client.client_email
                or analysis.fd_a_email_address
                or analysis.client_email
                or ""
            ).strip()
        else:
            payload["client_name"] = ""
    else:
        payload["client_name"] = ""
    return payload


def _client_call_history(client_id: str | None, db: Session):
    """Return every call tied to this client, regardless of the viewer's role.

    This is read-only cross-referencing (the Client Lifecycle view and the
    call detail page's history list), not editing, so it intentionally does
    not apply the per-extension assignment restriction that gates manual
    edits in assert_manual_call_access. The CallRecordsUsageGuide explicitly
    tells agents to cross-check a client's other calls across extensions to
    identify and verify their own, which the assignment filter used to block
    for any agent viewing a client whose calls span an extension they are
    not currently assigned to.
    """
    if not client_id:
        return []
    calls = (
        db.query(Call)
        .filter(Call.client_id == client_id)
        .order_by(Call.call_date.desc(), Call.call_time.desc())
        .all()
    )
    # A revised Vitel leg can share date/time/src/dst with the original and
    # differ only in billsec. Show one physical call consistently everywhere.
    unique_calls = {}
    for item in calls:
        key = (item.call_date, item.call_time, str(item.src or "").strip(), str(item.dst or "").strip())
        current = unique_calls.get(key)
        if current is None or (bool(item.audio_s3_uri or item.audio_s3_key), item.billsec or 0, -(item.call_number or 0)) > (bool(current.audio_s3_uri or current.audio_s3_key), current.billsec or 0, -(current.call_number or 0)):
            unique_calls[key] = item
    return [
        {
            "call_id": str(item.call_id),
            "call_reference": item.call_reference,
            "call_date": item.call_date.isoformat() if item.call_date else None,
            "call_time": item.call_time.isoformat() if item.call_time else None,
        }
        for item in unique_calls.values()
    ]


class ClientProfileUpdateRequest(BaseModel):
    client_name: Optional[str] = None
    client_email: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


@router.patch("/clients/{client_id}")
def update_client_profile(client_id: str, payload: ClientProfileUpdateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Edit the client_id-level name/email, the source of truth shown everywhere for this client."""
    client = db.query(Client).filter(Client.client_id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if payload.client_name is not None:
        client.client_name = payload.client_name.strip() or None
    if payload.client_email is not None:
        client.client_email = payload.client_email.strip() or None
    client.updated_at = datetime.now(timezone.utc)
    db.add(client)
    db.commit()
    return {"client": _serialize_client(client, db)}


def _calls_page_call_ids(client: Client, db: Session) -> set[str]:
    """Call ids of this client that the Calls page lists (same query as its
    Call Records). The client's other calls are the automated-attendant
    routing records the Calls page hides on purpose; they belong to the
    Missed Calls page."""
    from datetime import timedelta
    from services.analysis_read_query import analysis_read

    shown, _, _ = analysis_read(
        db, start=date(2000, 1, 1), end=date.today() + timedelta(days=1),
        client_id=client.client_id, call_source=True, target_page="calls",
    )
    return {str(call.call_id) for call in shown}


def _client_call_breakdown(client: Client, history: list[dict], db: Session, shown_ids: set[str] | None = None) -> dict:
    """Split a client's calls by the page they appear on (counts add up to the total).

    missed_call_entries is how many Missed Calls entries the hidden records make up.
    """
    import re
    from routes.calls import _missed_calls_page_count

    shown_ids = _calls_page_call_ids(client, db) if shown_ids is None else shown_ids
    calls_page = sum(1 for call in history if call["call_id"] in shown_ids)
    return {
        "calls_page": calls_page,
        "missed_call_page": max(len(history) - calls_page, 0),
        "missed_call_entries": _missed_calls_page_count(db, re.sub(r"\D", "", client.phone_number or ""), None, None)
        if client.phone_number else 0,
    }


@router.get("/clients/{client_id}/calls")
def get_client_call_history(client_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Return a client's calls in reverse chronological order."""
    client = db.query(Client).filter(Client.client_id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    history = _client_call_history(client_id, db)
    shown_ids = _calls_page_call_ids(client, db)
    return {
        "client": _serialize_client(client, db),
        # Each call says which page it belongs to, so the dialog can group them.
        "calls": [{**call, "on_calls_page": call["call_id"] in shown_ids} for call in history],
        "breakdown": _client_call_breakdown(client, history, db, shown_ids),
    }


@router.post("/calls/{call_id}/analyse")
def analyse_flintdesk_call(
    call_id: str,
    reanalyse: bool = False,
    request_id: Optional[uuid.UUID] = None,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Share the durable admission/recovery path with the background worker."""
    call = _get_call_or_404(call_id, db)
    _assert_call_access(call, user, db)
    if not (call.transcript or "").strip():
        raise HTTPException(status_code=400, detail="Transcript unavailable for FlintDesk analysis.")
    if reanalyse and request_id is None:
        raise HTTPException(status_code=400, detail="A request ID is required for re-analysis.")
    call_id = str(call.call_id)
    db.expunge(call)
    db.rollback()  # Release the request's read transaction before model I/O.
    try:
        F3Processor(application="flintdesk").process_record(
            call, request_key=str(request_id) if reanalyse else None, raise_errors=True,
        )
    except AnalysisBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AnalysisNeedsReview as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("FlintDesk analysis interrupted for call_id=%s", call_id)
        raise HTTPException(status_code=503, detail="Analysis interrupted. A saved response will be reused on retry; uncertain requests require review.") from exc
    analysis = db.query(CallAnalysisResult).filter(CallAnalysisResult.call_id == call_id).first()
    return {
        "call_id": str(call_id),
        "fd_processing_status": "processed",
        "analysis": {key: getattr(analysis, key, None) for key in FD_AI_FIELDS},
    }


@router.post("/calls/{call_id}/regenerate-stage2")
def regenerate_stage2(
    call_id: str,
    replace_manual: bool = False,
    user=Depends(require_fd_team_lead),
    db: Session = Depends(get_db),
):
    """Re-run only the Stage 2 AI fields (admin / team lead).

    By default values an agent typed are kept; replace_manual=true is the
    explicit opt-in to replace them. Needs STAGE2 AI enabled and active fd_prompts.
    """
    from services.fd_prompt_store import get_stage2_prompts
    from utils.config import STAGE2_AI_ENABLED
    from services.stage2_automation import apply_stage2_result, audit_event

    if not STAGE2_AI_ENABLED:
        raise HTTPException(status_code=409, detail="Stage 2 AI automation is not enabled.")
    call = _get_call_or_404(call_id, db)
    _assert_call_access(call, user, db)
    if not (call.transcript or "").strip():
        raise HTTPException(status_code=400, detail="Transcript unavailable for Stage 2 analysis.")
    prompts = get_stage2_prompts()
    if prompts is None:
        raise HTTPException(status_code=409, detail="Stage 2 prompts are not configured.")
    call_pk = call.call_id
    db.expunge(call)
    db.rollback()  # Release the read transaction before model I/O.
    try:
        result = F3Processor(application="flintdesk").llm.generate_stage2(call.transcript, call, prompts)
    except Exception as exc:
        logger.exception("Stage 2 regeneration failed for call_id=%s", call_pk)
        raise HTTPException(status_code=503, detail="Stage 2 generation failed; nothing was changed.") from exc
    if result is None:
        raise HTTPException(status_code=422, detail="The model did not return a usable result; nothing was changed.")
    db.execute(text("SELECT call_id FROM calls WHERE call_id=:id FOR UPDATE"), {"id": call_pk})
    analysis = db.query(CallAnalysisResult).filter(CallAnalysisResult.call_id == str(call_pk)).first()
    if analysis is None:
        analysis = _get_or_create_analysis_record(call, db)
    written = apply_stage2_result(analysis, result, result.get("prompt_versions") or {}, replace_manual=replace_manual)
    db.add(analysis)
    db.commit()
    audit_event(user, "FD_STAGE2_REGENERATED", "call", call_pk, category="admin",
          metadata={"fields": written, "replace_manual": replace_manual})
    return {"call_id": str(call_pk), "written": written,
            "skipped_manual": [f for f in ("fd_m_self_action_item", "fd_m_action_due_date", "fd_m_follow_up_dates") if f not in written]}


@router.patch("/calls/{call_id}/manual-fields")
def update_flintdesk_manual_fields(
    call_id: str,
    payload: FlintDeskManualFieldUpdateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update agent-editable FlintDesk Stage 1-4 data for one accessible call."""
    call = _get_call_or_404(call_id, db)
    supplied_fields = (
        payload.model_fields_set
        if hasattr(payload, "model_fields_set")
        else payload.__fields_set__
    )
    if not supplied_fields.issubset(CALLS_PAGE_AGENT_EDITABLE_FIELDS):
        raise HTTPException(status_code=422, detail="This Calls-page field cannot be edited.")
    _reject_attachment_path_write({field: getattr(payload, field) for field in supplied_fields & ATTACHMENT_FIELDS})
    _assert_calls_page_edit_access(user)
    analysis = _get_or_create_analysis_record(call, db)

    # Stage 2 is the mandatory operational hand-off.  Do not permit a
    # multi-field request (including an import) to add Stage 3/4 work until
    # it is complete.  Use the submitted Stage 2 values as well so an atomic
    # request that genuinely completes Stage 2 and starts Stage 3 is valid.
    if supplied_fields.intersection(STAGE34_FIELDS):
        proposed = {field: getattr(payload, field) for field in supplied_fields}
        missing = missing_stage2_fields(analysis, proposed)
        if missing:
            raise HTTPException(
                status_code=409,
                detail="Complete all Stage 2 fields before editing Stage 3 or Stage 4: " + ", ".join(missing),
            )

    # Preserve the LLM-generated values as the starting point, while allowing
    # agents to correct every Stage 1 field (including a field the LLM left
    # blank).  The row is shared by Calls and Case Closing, so one commit keeps
    # both views consistent.
    # "Mark Completed" certifies the Stage 1 call-analysis data (FD_AI_FIELDS)
    # as reviewed. If any of that data changes after the case was marked
    # completed, the certification no longer describes what's on record, so
    # completion is cleared in this same update rather than left stale.
    completion_relevant_changed = False
    for field in FD_AI_FIELDS:
        if field in supplied_fields:
            value = getattr(payload, field)
            normalized_value = value.strip() or None if isinstance(value, str) else value
            if normalized_value != getattr(analysis, field):
                completion_relevant_changed = True
            setattr(analysis, field, normalized_value)

    if payload.fd_m_client_name is not None:
        analysis.fd_m_client_name = payload.fd_m_client_name.strip() or None

    if payload.fd_m_self_action_item is not None:
        analysis.fd_m_self_action_item = payload.fd_m_self_action_item.strip()

    if payload.fd_m_action_due_date is not None:
        analysis.fd_m_action_due_date = payload.fd_m_action_due_date
    if payload.fd_m_action_due_note is not None:
        analysis.fd_m_action_due_note = payload.fd_m_action_due_note.strip() or None

    if payload.fd_m_manual_action_status is not None:
        analysis.fd_m_manual_action_status = payload.fd_m_manual_action_status.strip()

    if payload.fd_m_dependency is not None:
        analysis.fd_m_dependency = payload.fd_m_dependency.strip()

    if payload.fd_m_follow_up_dates is not None:
        analysis.fd_m_follow_up_dates = payload.fd_m_follow_up_dates.strip()

    if payload.fd_m_follow_up_attempts is not None:
        analysis.fd_m_follow_up_attempts = payload.fd_m_follow_up_attempts

    if payload.fd_m_blocker_comment is not None:
        analysis.fd_m_blocker_comment = payload.fd_m_blocker_comment.strip()

    if payload.fd_m_case_status_change is not None:
        value = accepted_status(payload.fd_m_case_status_change, analysis.fd_m_case_status_change)
        if value is None:
            raise HTTPException(status_code=400, detail="Invalid fd_m_case_status_change value.")
        db.add(analysis)
        db.flush()
        if (analysis.fd_m_case_status_change or "").strip() != value:
            analysis.fd_m_date_entry = case_status_change_date()
        _record_status_change(db, analysis, analysis.fd_m_case_status_change, value, user)
        previous_case_status = analysis.fd_m_case_status_change
        analysis.fd_m_case_status_change = value
        # Cross-agent closure sync (see services/case_closure.py); never fails the save.
        db.flush()
        case_closure.on_status_saved(db, analysis, call, user, previous_case_status, value)

    # fd_m_date_entry (Date of Case Status Change) is system-generated above;
    # a client-supplied value is ignored.

    if payload.fd_m_verification_comment is not None:
        analysis.fd_m_verification_comment = payload.fd_m_verification_comment.strip()

    if payload.fd_m_attachment is not None:
        analysis.fd_m_attachment = payload.fd_m_attachment.strip()

    if payload.fd_m_stage4_attachment is not None:
        analysis.fd_m_stage4_attachment = payload.fd_m_stage4_attachment.strip()

    if payload.fd_m_google_review is not None:
        analysis.fd_m_google_review = payload.fd_m_google_review.strip()

    if payload.front_desk_person_name is not None:
        normalized_agent_name = payload.front_desk_person_name.strip() or None
        _assert_agent_self_identification("front_desk_person_name", normalized_agent_name, user, call=call, db=db)
        analysis.front_desk_person_name = normalized_agent_name
        # Manual agent-name shadow column disabled (see migration 064).
        # analysis.front_desk_person_name_manual = normalized_agent_name

    if supplied_fields.intersection(FD_MANUAL_FIELDS):
        analysis.manual_data_score = None
        analysis.manual_data_score_reason = None
        analysis.manual_data_scored_at = None

    if payload.client_status is not None:
        value = payload.client_status.strip()
        if value not in VALID_CLIENT_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid client_status value.")
        if not call.client:
            raise HTTPException(status_code=409, detail="This historical call has no resolved client.")
        call.client.client_status = value
        call.client.updated_at = datetime.now(timezone.utc)
        db.add(call.client)

    if completion_relevant_changed and analysis.case_closing_completed:
        analysis.case_closing_completed = False
        analysis.case_closing_completed_at = None
    # A completed call can no longer honestly be marked complete if a
    # required Stage 2 hand-off field was cleared in this update.
    if supplied_fields.intersection(STAGE2_REQUIRED_FIELDS) and missing_stage2_fields(analysis):
        analysis.case_closing_completed = False
        analysis.case_closing_completed_at = None

    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    return {
        "call_id": str(call.call_id),
        "client": _serialize_client(call.client, db),
        "fd_processing_status": call.fd_processing_status or "not processed",
        "analysis": {key: getattr(analysis, key) for key in FD_AI_FIELDS},
        "manual": {key: getattr(analysis, key) for key in FD_MANUAL_FIELDS},
        "case_closing_completed": analysis.case_closing_completed,
        "case_closing_completed_at": (
            analysis.case_closing_completed_at.isoformat()
            if analysis.case_closing_completed_at else None
        ),
    }


class CallImport(BaseModel):
    # Same shape as Leads'/Tasks' /import bodies: one dict per row, keyed by
    # recognized column name. Every record must include "call_id" (a UUID or
    # AF# call_reference identifying an EXISTING call -- see
    # IMPORT_RECOGNIZED_FIELDS/_get_call_or_404); this endpoint only edits
    # Stage 1-4 data on calls that already exist from the normal Vitel sync,
    # it never fabricates a new Call row.
    records: list[dict[str, Any]] = Field(min_length=1, max_length=2000)


# The exact set of columns already present in the Calls tool (Stage 1's AI
# Intake fields, Stage 2/3/4's manual fields, plus the two extra fields the
# single-record editor also accepts) -- the same allow-list
# update_flintdesk_manual_fields already enforces. No new columns are
# introduced for import; "call_id" is the one additional, required identity
# column used only to find the existing call, not stored anywhere itself.
IMPORT_RECOGNIZED_FIELDS = CALLS_PAGE_AGENT_EDITABLE_FIELDS - {"fd_m_date_entry"}
IMPORT_DATE_FIELDS = DATE_ONLY_FIELDS
IMPORT_INT_FIELDS = {"fd_m_follow_up_attempts"}

# "Call_ID" is the only column import actually needs to find the row -- it's
# unique per call. Date and Client Phone Number are optional, verification-
# only columns: if a pasted row includes either, it's checked against this
# call's real recorded date/number (a safeguard against copy-pasting into
# the wrong row from a wide Excel sheet) and the row is skipped, like any
# other invalid value, if it doesn't match. They're never written to any
# column themselves, so they're kept out of IMPORT_RECOGNIZED_FIELDS/
# FlintDeskManualFieldUpdateRequest entirely.
IMPORT_VERIFICATION_FIELDS = {"import_call_date", "import_client_phone"}


def _create_manual_call(db: Session, call_date: date | None, phone: str | None) -> Call:
    """Create a brand-new call record for an Add/Import row with no Call ID.

    There is no existing Call to update -- either PlanDesk/FlintDesk was
    down and the agent has no Call ID at all, or the client has no phone
    number on file and the call was never captured by the normal Vitel
    sync. Either way this gets a fresh identity, allocated the same way F1
    allocates one for a real synced call (call_number_seq + "AF#" reference),
    but none of Stage 1's AI intake or call-detail columns -- there was
    never a recording for those to come from. Stage 2-4 fields land on it
    exactly like any other call, through the same
    update_flintdesk_manual_fields call the rest of this import already uses.
    """
    call_number = db.execute(text("SELECT nextval('call_number_seq')")).scalar_one()
    resolved_date = call_date or datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc)
    client = resolve_client_for_call(db, None, phone, resolved_date) if phone else None
    call = Call(
        call_id=uuid.uuid4(),
        call_number=call_number,
        call_reference=f"AF#{call_number}",
        call_date=resolved_date,
        call_time=now.time(),
        dst=phone or None,
        client_id=client.client_id if client else None,
        registered_at=now,
        updated_at=now,
    )
    db.add(call)
    db.flush()
    return call


@router.post("/calls/import")
def import_calls(payload: CallImport, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Bulk-import Stage 1-4 data onto calls, identified by Call ID.

    Reuses update_flintdesk_manual_fields per row (same field allow-list,
    same VALID_FD_CASE_STATUS_CHANGE/VALID_CLIENT_STATUSES validation, same
    case-status-history and manual-score-reset behavior) instead of a
    second, parallel field-writing path -- an import is just many single
    edits made at once. Access matches that same endpoint
    (_assert_calls_page_edit_access): every authenticated FlintDesk user,
    agents included, may import -- there is no admin/team-lead/SME gate on
    Calls-page field edits in this codebase, unlike Leads/Tasks imports.

    A row with no Call ID has no existing call to update (the agent has no
    way to know one -- see the Calls page's Edit modal, which always
    targets the call whose Edit icon was clicked, for that case instead).
    It is instead treated as a genuinely new, manually-documented call and
    a fresh Call row is created for it via _create_manual_call.

    Lenient like Tasks' /import (not Leads' all-or-nothing): a row with an
    invalid Call ID or an invalid field value is skipped and reported, it
    does not abort the rest of the import -- each row that does succeed is
    committed durably before the next one is attempted.
    """
    _assert_calls_page_edit_access(user)
    imported = 0
    updated_records: list[dict[str, Any]] = []
    invalid_fields: list[dict[str, Any]] = []
    skipped_empty = 0

    for row_number, record in enumerate(payload.records, start=2):
        call_ref = str(record.get("call_id") or "").strip()
        field_values = {
            key: value
            for key, value in record.items()
            if key != "call_id"
            and (key in IMPORT_RECOGNIZED_FIELDS or key in IMPORT_VERIFICATION_FIELDS)
            and str(value if value is not None else "").strip() != ""
        }
        verification_date = field_values.pop("import_call_date", None)
        verification_phone = field_values.pop("import_client_phone", None)

        if not call_ref and not field_values:
            skipped_empty += 1
            continue
        if not field_values:
            skipped_empty += 1
            continue

        if call_ref:
            try:
                call = _get_call_or_404(call_ref, db)
            except HTTPException:
                invalid_fields.append({"row": row_number, "field": "call_id", "reason": f'No existing call found for "{call_ref}"'})
                continue

            if verification_date:
                try:
                    parsed_date = _parse_calls_date(verification_date, "Date")
                except HTTPException:
                    invalid_fields.append({"row": row_number, "field": "Date", "reason": "Invalid date format"})
                    continue
                if parsed_date and parsed_date != call.call_date:
                    invalid_fields.append({
                        "row": row_number, "field": "Date",
                        "reason": f'Date does not match call "{call_ref}"\'s recorded date -- row skipped.',
                    })
                    continue

            if verification_phone:
                normalized_input = _phone(verification_phone)
                if normalized_input and normalized_input not in {_phone(call.src), _phone(call.dst)}:
                    invalid_fields.append({
                        "row": row_number, "field": "Client Phone Number",
                        "reason": f'Client Phone Number does not match call "{call_ref}"\'s number -- row skipped.',
                    })
                    continue
        else:
            # No Call ID -- nothing to verify verification_date/
            # verification_phone against, so they instead become this new
            # call's actual date/number (a phone number is optional; a date
            # falls back to today). Hold off on actually creating the Call
            # row (a real INSERT) until every other way this row could still
            # turn out invalid/empty has already been checked below --
            # otherwise a row that fails later validation would still have
            # allocated and left behind an empty, dataless call.
            call = None
            new_call_date = None
            if verification_date:
                try:
                    new_call_date = _parse_calls_date(verification_date, "Date")
                except HTTPException:
                    invalid_fields.append({"row": row_number, "field": "Date", "reason": "Invalid date format"})
                    continue

        row_had_error = False
        for field in IMPORT_DATE_FIELDS & field_values.keys():
            try:
                field_values[field] = parse_task_date(field_values[field], field)
            except HTTPException:
                invalid_fields.append({"row": row_number, "field": field, "reason": "Invalid date format"})
                field_values.pop(field)
                row_had_error = True
        for field in IMPORT_INT_FIELDS & field_values.keys():
            try:
                field_values[field] = int(str(field_values[field]).strip())
            except (TypeError, ValueError):
                invalid_fields.append({"row": row_number, "field": field, "reason": "Must be a number"})
                field_values.pop(field)
                row_had_error = True

        if not field_values:
            if row_had_error:
                continue
            skipped_empty += 1
            continue

        try:
            update_payload = FlintDeskManualFieldUpdateRequest(**field_values)
        except ValidationError as exc:
            bad_field = str(exc.errors()[0]["loc"][-1]) if exc.errors() else "call_id"
            invalid_fields.append({"row": row_number, "field": bad_field, "reason": "Invalid value"})
            continue

        if call is None:
            try:
                call = _create_manual_call(db, new_call_date, verification_phone)
            except Exception:
                db.rollback()
                invalid_fields.append({"row": row_number, "field": "call_id", "reason": "Unable to create a new call record."})
                continue

        try:
            update_flintdesk_manual_fields(call_id=str(call.call_id), payload=update_payload, user=user, db=db)
            imported += 1
            # Report the resolved identity, not merely a count.  This makes
            # an import auditable even when the spreadsheet used a UUID in
            # Call_ID and a short AF number in Call ID.
            updated_records.append({
                "call_id": str(call.call_id),
                "call_reference": call.call_reference,
                "updated_fields": sorted(field_values.keys()),
            })
        except HTTPException as exc:
            db.rollback()
            invalid_fields.append({"row": row_number, "field": "call_id", "reason": str(exc.detail)})

    return {
        "imported": imported,
        "updated_records": updated_records,
        "invalid_fields": invalid_fields,
        "skipped_empty": skipped_empty,
    }


@router.patch("/calls/{call_id}/field")
def update_flintdesk_single_field(
    call_id: str,
    payload: FlintDeskSingleFieldUpdateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Persist one Calls-page field with a compare-and-set stale-write guard.

    This route deliberately returns no full call/client payload and does no
    analysis work. It is safe for concurrent edits to different fields because
    it updates only the requested column. Two edits to the same field are
    compared against the value displayed when editing began; a stale request
    receives 409 instead of overwriting the newer value.
    """
    field = str(payload.field or "").strip()
    if field not in FAST_EDITABLE_FIELDS:
        raise HTTPException(status_code=422, detail="This Calls-page field cannot be edited.")
    if field == "fd_m_date_entry":
        raise HTTPException(status_code=422, detail="Date of Case Status Change is set automatically when Case Status changes.")
    if field in ATTACHMENT_FIELDS:
        _reject_attachment_path_write({field: payload.value})

    _assert_calls_page_edit_access(user)

    # Viewing is shared, but non-privileged agents may edit only outbound
    # calls on their assigned extension. Inbound calls stay shared so an
    # agent can identify and document the call they answered.
    permission_call = _get_call_or_404(call_id, db)
    _assert_call_access(permission_call, user, db)

    resolved_call_id: str | None = None
    try:
        resolved_call_id = str(uuid.UUID(str(call_id)))
    except (ValueError, TypeError):
        pass

    analysis = None
    if resolved_call_id is not None:
        analysis = (
            db.query(CallAnalysisResult)
            .filter(CallAnalysisResult.call_id == resolved_call_id)
            .first()
        )
    if analysis is None:
        resolved_call_id = str(permission_call.call_id)
        analysis = _get_or_create_analysis_record(permission_call, db)

    if field in STAGE34_FIELDS:
        missing = missing_stage2_fields(analysis)
        if missing:
            raise HTTPException(
                status_code=409,
                detail="Complete all Stage 2 fields before editing Stage 3 or Stage 4: " + ", ".join(missing),
            )

    # Compare canonical values on *both* sides.  Older imports and the
    # pre-inline-editor endpoints could leave whitespace-only strings in text
    # columns.  The Calls UI correctly renders those as blank, then sends an
    # empty expected value; comparing that against the raw ``"   "`` value
    # made a field that visibly looked empty fail forever as a stale edit.
    # Normalizing the stored value as well retains compare-and-set protection
    # for meaningful changes while treating equivalent representations of an
    # empty/text value consistently.
    expected_value = _normalize_fast_field_value(field, payload.expected_value)
    current_value = getattr(analysis, field)
    normalized_current_value = _normalize_fast_field_value(field, current_value)

    # The client's name and email are shown from the client profile (the source
    # of truth for the client), not from this call's own copy of the field.
    # Compare against what the table displayed, otherwise a profile that was
    # filled or changed after this call was analysed makes every later edit of
    # that field fail as "updated by another user" even though nobody else
    # touched it; and write the change to the profile so what the agent typed
    # is what is shown next time.
    profile = None
    profile_attr = PROFILE_BACKED_FIELDS.get(field)
    client_id = getattr(permission_call, "client_id", None)
    if profile_attr and client_id:
        profile = db.query(Client).filter(Client.client_id == client_id).first()
    profile_value = _normalize_fast_field_value(field, getattr(profile, profile_attr, None)) if profile else None
    shown_value = profile_value if profile_value is not None else normalized_current_value
    if shown_value != expected_value:
        raise HTTPException(
            status_code=409,
            detail="This field was updated by another user. Reload the latest value and try again.",
        )

    normalized_value = _normalize_fast_field_value(field, payload.value)
    _assert_agent_self_identification(field, normalized_value, user, call=permission_call, db=db)
    if field == "fd_m_case_status_change" and normalized_value is not None:
        normalized_value = accepted_status(normalized_value, current_value)
        if normalized_value is None:
            raise HTTPException(status_code=422, detail="Invalid fd_m_case_status_change value.")
        db.add(analysis)
        db.flush()
        _record_status_change(db, analysis, current_value, normalized_value, user)
        if (current_value or "").strip() != normalized_value:
            analysis.fd_m_date_entry = case_status_change_date()
    elif field == "fd_m_case_status_change" and normalized_value is None and current_value:
        analysis.fd_m_date_entry = None

    setattr(analysis, field, normalized_value)
    if field == "fd_m_case_status_change":
        # Cross-agent closure sync (see services/case_closure.py); never fails the save.
        db.flush()
        case_closure.on_status_saved(db, analysis, permission_call, user, current_value, normalized_value)
    if profile is not None:
        setattr(profile, profile_attr, normalized_value)
        profile.updated_at = datetime.now(timezone.utc)
        db.add(profile)
    # Manual agent-name shadow column disabled (see migration 064).
    # if field == "front_desk_person_name":
    #     # Model-proof copy; a DB trigger restores the display column from it.
    #     analysis.front_desk_person_name_manual = normalized_value
    if field in FD_MANUAL_FIELDS:
        analysis.manual_data_score = None
        analysis.manual_data_score_reason = None
        analysis.manual_data_scored_at = None

    # "Mark Completed" certifies the Stage 1 call-analysis data (FD_AI_FIELDS)
    # as reviewed. Changing any of that data on a completed call means the
    # certification no longer describes what's on record, so it's cleared
    # here -- in this same update -- instead of left stale. Re-completing is
    # a separate, explicit action (routes.calls.update_manual_fields).
    value_changed = normalized_value != (shown_value if profile is not None else current_value)
    if field in FD_AI_FIELDS and value_changed and analysis.case_closing_completed:
        analysis.case_closing_completed = False
        analysis.case_closing_completed_at = None
    if field in STAGE2_REQUIRED_FIELDS and missing_stage2_fields(analysis):
        analysis.case_closing_completed = False
        analysis.case_closing_completed_at = None

    db.add(analysis)
    db.commit()
    return {
        "call_id": resolved_call_id,
        "field": field,
        "normalized_value": _display_fast_field_value(field, normalized_value),
        # Set by the server whenever Case Status changes; echoed so the table
        # shows it without a reload.
        "fd_m_date_entry": analysis.fd_m_date_entry.isoformat() if analysis.fd_m_date_entry else None,
        "case_closing_completed": analysis.case_closing_completed,
        "case_closing_completed_at": (
            analysis.case_closing_completed_at.isoformat()
            if analysis.case_closing_completed_at else None
        ),
        # Returned when the edit changed the client profile, so the table can
        # show the new value on all of this client's calls without a reload.
        "client": (
            {
                "client_id": profile.client_id,
                "client_name": profile.client_name or "",
                "client_email": profile.client_email or "",
            }
            if profile is not None else None
        ),
    }


def _validate_attachment_target(target: str) -> str:
    if target not in {"fd_m_attachment", "fd_m_stage4_attachment"}:
        raise HTTPException(status_code=400, detail="Invalid attachment target.")
    return target


ATTACHMENT_FIELDS = {"fd_m_attachment", "fd_m_stage4_attachment"}


def _reject_attachment_path_write(fields_and_values: dict) -> None:
    """Attachment columns hold server-generated Storage paths only.

    Letting a client write an arbitrary string there would let it point one
    call's attachment at another call's object and have the server sign or
    delete it. Clearing (None) stays allowed; uploads go through the upload
    endpoint.
    """
    for field in ATTACHMENT_FIELDS:
        if fields_and_values.get(field) not in (None, ""):
            raise HTTPException(status_code=422, detail="Attachments can only be changed through the upload endpoint.")


def _owned_storage_path(call: Call, value: str | None) -> bool:
    """True only for a Supabase path that lives under this call's own folder."""
    if not _is_supabase_storage_path(value) or ".." in value:
        return False
    prefixes = {f"{call.call_id}/"}
    if call.call_reference:
        prefixes.add(f"{call.call_reference}/")
    return any(value.startswith(prefix) for prefix in prefixes)


def _is_supabase_storage_path(value: str | None) -> bool:
    # Legacy rows may still hold an old s3:// URI; only Supabase-style
    # relative paths ("{call_id}/{filename}") should be deleted via Storage.
    return bool(value) and not value.startswith("s3://") and not value.startswith("http")


@router.post("/calls/{call_id}/attachment")
async def upload_flintdesk_attachment(
    call_id: str,
    user=Depends(get_current_user),
    file: UploadFile = File(...),
    target: str = Query("fd_m_attachment"),
    db: Session = Depends(get_db),
):
    """Upload an optional screenshot/image to Supabase Storage and persist only its path."""
    call = _get_call_or_404(call_id, db)
    _assert_call_access(call, user, db)
    analysis = _get_or_create_analysis_record(call, db)
    target = _validate_attachment_target(target)
    if target in STAGE34_FIELDS:
        missing = missing_stage2_fields(analysis)
        if missing:
            raise HTTPException(
                status_code=409,
                detail="Complete all Stage 2 fields before editing Stage 3 or Stage 4: " + ", ".join(missing),
            )

    if not file.filename:
        raise HTTPException(status_code=400, detail="Attachment filename is required.")

    extension = os.path.splitext(file.filename)[1].lower()
    if extension not in ALLOWED_ATTACHMENT_EXTENSIONS or (file.content_type and file.content_type not in ALLOWED_ATTACHMENT_CONTENT_TYPES):
        raise HTTPException(status_code=400, detail="Unsupported attachment type. Allowed: .jpg, .jpeg, .png")

    content = await file.read()
    if len(content) > MAX_ATTACHMENT_SIZE_BYTES:
        raise HTTPException(status_code=400, detail=f"Attachment exceeds the {MAX_ATTACHMENT_SIZE_MB:.0f}MB size limit.")

    previous_path = getattr(analysis, target)
    storage_path = f"{call.call_id}/{uuid.uuid4().hex}{extension}"
    content_type = file.content_type or "image/png"

    try:
        storage = SupabaseStorageService()
        storage.upload_bytes(storage_path, content, content_type)
        # Replacing an attachment: remove the old file so Storage doesn't
        # accumulate orphaned objects for this call/case.
        if _owned_storage_path(call, previous_path):
            storage.delete(previous_path)
    except Exception as exc:
        logger.exception("Attachment upload failed")
        raise HTTPException(status_code=500, detail="Attachment upload failed.") from exc

    setattr(analysis, target, storage_path)
    analysis.manual_data_score = None
    analysis.manual_data_score_reason = None
    analysis.manual_data_scored_at = None
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    return {
        "call_id": str(call.call_id),
        target: storage_path,
    }


@router.get("/calls/{call_id}/attachment-url")
def get_flintdesk_attachment_url(
    call_id: str,
    target: str = Query("fd_m_attachment"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns a short-lived signed URL so attachments stay in a private bucket."""
    call = _get_call_or_404(call_id, db)
    _assert_call_access(call, user, db)
    analysis = _get_or_create_analysis_record(call, db)
    target = _validate_attachment_target(target)

    stored_path = getattr(analysis, target)
    if not _owned_storage_path(call, stored_path):
        raise HTTPException(status_code=404, detail="No attachment available for this call.")

    try:
        signed_url = SupabaseStorageService().create_signed_url(stored_path)
    except Exception as exc:
        logger.exception("Failed to generate attachment URL")
        raise HTTPException(status_code=500, detail="Failed to generate attachment URL.") from exc

    return {"url": signed_url}


@router.delete("/calls/{call_id}/attachment")
def delete_flintdesk_attachment(
    call_id: str,
    target: str = Query("fd_m_attachment"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Clears an existing attachment (optional field) and removes the Storage object."""
    call = _get_call_or_404(call_id, db)
    _assert_call_access(call, user, db)
    analysis = _get_or_create_analysis_record(call, db)
    target = _validate_attachment_target(target)
    if target in STAGE34_FIELDS:
        missing = missing_stage2_fields(analysis)
        if missing:
            raise HTTPException(
                status_code=409,
                detail="Complete all Stage 2 fields before editing Stage 3 or Stage 4: " + ", ".join(missing),
            )

    stored_path = getattr(analysis, target)
    if _owned_storage_path(call, stored_path):
        try:
            SupabaseStorageService().delete(stored_path)
        except Exception as exc:
            logger.exception("Failed to delete attachment")
            raise HTTPException(status_code=500, detail="Failed to delete attachment.") from exc

    setattr(analysis, target, None)
    analysis.manual_data_score = None
    analysis.manual_data_score_reason = None
    analysis.manual_data_scored_at = None
    db.add(analysis)
    db.commit()

    return {"call_id": str(call.call_id), target: None}
