from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import re
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session, joinedload

from middleware.auth import get_current_user
from routes.calls import _agent_names_with_manual_override, _call_direction, _system_numbers
from storage.db import get_db
from storage.models import Call, CallAnalysisResult, Client, FDLead, FDTask, FDTaskNotification, FDUser
from storage.s3 import S3Service
from utils.config import MISSED_CALLS_S3_WORKERS

router = APIRouter(prefix="/api/flintdesk/tasks", tags=["FlintDesk Tasks"])
FIELDS = {"date", "tasks", "timeline", "owner", "status", "comments", "missed_call_date", "miscall_added_date", "name", "contact_number", "summary", "action_status", "date_added", "type", "phone_number", "email"}
EDITOR_ROLES = {"admin", "team_lead", "subject_matter_expert", "superuser", "super_user"}
CALL_TASK_TYPES = {"missed_call", "non_responsiveness"}
# storage/s3.py's own download_file() already treats an audio object under
# this size as "corrupted/empty -- likely a failed recording"; the Missed
# Calls page's Called Back view reuses that exact, already-established
# threshold rather than inventing a new one.
EMPTY_AUDIO_BYTES_THRESHOLD = 1024
# Phone field that identifies the caller for each call-linked task type.
CALL_TASK_PHONE_FIELD = {"missed_call": "contact_number", "non_responsiveness": "phone_number"}


def _task_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else digits


def _task_relevant_date(task: FDTask) -> date | None:
    """The date a call task actually happened, for matching it to a Call.

    missed_call_date is frequently left blank in practice -- the date the
    task was logged (miscall_added_date) is what's actually populated for
    most Missed Call rows -- so fall back to it rather than matching on a
    field that's usually empty.
    """
    if task.task_type == "missed_call":
        return task.fd_m_tasks_missed_call_date or task.fd_m_tasks_miscall_added_date
    if task.task_type == "non_responsiveness":
        return task.fd_m_tasks_date_added
    return None


def _same_call_occurrence(left: FDTask, right: FDTask) -> bool:
    """Return whether two task rows describe the same real-world call.

    A caller can legitimately contact us again on a later day. Phone number
    alone is not an assignment key. Auto-created tasks carry the source call
    UUID; older/manual rows fall back to caller number plus business date.
    """
    left_call_id = getattr(left, "fd_m_tasks_call_id", None)
    right_call_id = getattr(right, "fd_m_tasks_call_id", None)
    if left_call_id and right_call_id:
        return left_call_id == right_call_id
    left_phone = _task_phone(getattr(left, f"fd_m_tasks_{CALL_TASK_PHONE_FIELD[left.task_type]}", None))
    right_phone = _task_phone(getattr(right, f"fd_m_tasks_{CALL_TASK_PHONE_FIELD[right.task_type]}", None))
    return (
        bool(left_phone)
        and left_phone == right_phone
        and _task_relevant_date(left) is not None
        and _task_relevant_date(left) == _task_relevant_date(right)
    )


def _closest_call(calls: list[Call], task_date: date | None) -> Call | None:
    """Pick the call whose date is nearest the task's date.

    Missed Call tasks are commonly logged (miscall_added_date) a day or two
    after the actual callback happened, not the same day -- an exact-date
    match misses most of them for that reason alone. Nearest-date instead
    of exact still avoids matching a call from weeks/months earlier just
    because it's the newest one ever made from that number.
    """
    if not calls:
        return None
    if not task_date:
        return max(calls, key=lambda c: (c.call_date, c.call_time))
    return min(
        calls,
        key=lambda c: (abs((c.call_date - task_date).days), -_time_to_seconds(c.call_time)),
    )


def _time_to_seconds(value) -> int:
    if not value:
        return 0
    return value.hour * 3600 + value.minute * 60 + value.second


def _match_call_for_task(db: Session, phone: str, task_date: date | None) -> Call | None:
    """Best-effort lookup of the call this task actually describes.

    Missed-call/non-responsive rows are manually logged and are not stored
    with a call reference, so the match is done by phone number and the
    task's own date -- a phone-only match could otherwise pick an unrelated
    call from a completely different time period just because it's the
    newest call ever made from that number, showing the wrong
    summary/transcript/audio. See _closest_call for why "nearest date"
    rather than an exact match.
    """
    digits = _task_phone(phone)
    if not digits:
        return None
    calls = db.query(Call).filter(or_(Call.src.like(f"%{digits}"), Call.dst.like(f"%{digits}"))).all()
    return _closest_call(calls, task_date)


def _callback_match(calls: list[Call], task_date: date | None, system_numbers: set[str]) -> Call | None:
    """Find the outbound call that answers a Missed Call, for the Missed Calls page's Called Back view.

    A callback is qualifying evidence only when it is an OUTBOUND call to
    this client on the same business calendar date as the missed call.  An
    inbound call, or a callback from another day, must not make the Called
    Back row green.  call_date is already FlintDesk's business date.
    """
    if not task_date or not calls:
        return None
    candidates = [
        c
        for c in calls
        if c.call_date == task_date and _call_direction(c, system_numbers) == "Outbound"
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda c: (c.call_date, -_time_to_seconds(c.call_time)))


def _audio_byte_size(call: Call) -> int | None:
    """Best-effort S3 size check for the Called Back view's attended/pending
    coloring. Returns None (treated as "can't tell, don't penalize") if
    there is no audio file or the S3 lookup fails for any reason -- a
    transient S3 error must never misreport a real recording as empty.
    """
    uri = call.audio_s3_uri or call.audio_s3_key
    if not uri:
        return None
    try:
        return S3Service().get_file_size(uri)
    except Exception:
        return None


def _audio_byte_sizes_by_uri(calls: list[Call]) -> dict[str, int]:
    """Batch counterpart to _audio_byte_size, for the Called Back view's
    list endpoint below. S3 has no bulk HEAD API, so this still issues one
    request per distinct audio file -- but concurrently (this was the
    actual measured cause of Called Back's slow load: one HEAD request per
    matched callback call, done one after another in a Python loop, so wall
    time scaled linearly with row count). Runs them in a thread pool
    instead, since each is an independent, I/O-bound network call -- the
    same pattern services/Database_sync/f1.py already uses for its own
    audio downloads (F1_IO_WORKERS). De-duplicates by URI first so two
    tasks that happen to share one callback call's audio only cost one
    request.
    """
    uris = {uri for call in calls if (uri := call.audio_s3_uri or call.audio_s3_key)}
    if not uris:
        return {}
    with ThreadPoolExecutor(max_workers=MISSED_CALLS_S3_WORKERS, thread_name_prefix="missed-calls-s3") as executor:
        results = executor.map(lambda uri: (uri, _audio_byte_size_for_uri(uri)), uris)
    return {uri: size for uri, size in results if size is not None}


def _audio_byte_size_for_uri(uri: str) -> int | None:
    try:
        return S3Service().get_file_size(uri)
    except Exception:
        return None


class Payload(BaseModel):
    task_type: str
    data: dict[str, Any]
    submit: bool = False


class Bulk(BaseModel):
    task_type: str
    records: list[dict[str, Any]]


class BulkDelete(BaseModel):
    task_type: str
    ids: list[UUID]


class BulkAssign(BaseModel):
    task_type: str
    ids: list[UUID]
    owner_id: UUID


def normalized_role(user: dict) -> str:
    """Return the one canonical role value used by every Tasks mutation."""
    role = re.sub(r"[\s-]+", "_", str(user.get("role", "")).strip().lower())
    aliases = {
        "teamlead": "team_lead",
        "team_lead": "team_lead",
        "sme": "subject_matter_expert",
        "subjectmatterexpert": "subject_matter_expert",
        "subject_matter_expert": "subject_matter_expert",
    }
    return aliases.get(role, role)


def can_edit(user: dict, db: Session | None = None) -> bool:
    """Check Tasks editor access from the active persisted FlintDesk user.

    The JWT carries a role for the UI, but mutations must not depend on a
    stale/legacy role spelling in that token.  When a database session is
    available, the active ``fd_users`` record is the authorization source.
    """
    if db is None:
        return normalized_role(user) in EDITOR_ROLES
    try:
        persisted_user = db.get(FDUser, current_fd_user_id(user))
    except HTTPException:
        return False
    return bool(persisted_user and persisted_user.is_active and normalized_role({"role": persisted_user.role}) in EDITOR_ROLES)


def current_fd_user_id(user: dict) -> UUID:
    try:
        return UUID(str(user["fd_user_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(401, "FlintDesk user identity is required") from exc


# "Agent name in call" on the Missed Calls page can only ever be one of these.
AGENT_NAMES_IN_CALL = ("Akshita", "Priyanka", "Shrishti", "Gokul", "Christobel", "Edgin", "Gracy")


def _agent_names_for_transcripts(db: Session) -> list[str]:
    return list(AGENT_NAMES_IN_CALL)


def agent_names_in_transcript(transcript: str | None, names: list[str]) -> str:
    """Agent/staff names spoken in the transcript, in order of first mention.

    A name matches as a whole word, either in full or by its first name.
    Returns "N/A" when none are mentioned.
    """
    text_value = str(transcript or "")
    if not text_value.strip():
        return "N/A"
    found: dict[str, int] = {}
    for name in names:
        parts = name.split()
        candidates = {name} | ({parts[0]} if len(parts[0]) >= 3 else set())
        for candidate in candidates:
            match = re.search(rf"(?<![A-Za-z]){re.escape(candidate)}(?![A-Za-z])", text_value, re.IGNORECASE)
            if match:
                found.setdefault(name, match.start())
                break
    # Mary Kennedy/Kennady are one person; keep the first spelling only.
    if "Mary Kennedy" in found and "Mary Kennady" in found:
        del found["Mary Kennady"]
    seen_first: set[str] = set()
    ordered = []
    for name, _ in sorted(found.items(), key=lambda item: item[1]):
        first = name.split()[0].lower()
        if first in seen_first:
            continue
        seen_first.add(first)
        ordered.append(name)
    return ", ".join(ordered) if ordered else "N/A"


def serialize(
    task: FDTask,
    db: Session | None = None,
    # OPTIMIZATION: Accept pre-loaded lookup dictionaries to eliminate N+1 queries
    # When batch processing tasks, caller loads all related data once and passes lookups
    # Keyed by phone digits -> every call from that number; see _closest_call.
    calls_by_phone: dict[str, list[Call]] | None = None,
    # Keyed by call_id -- only needed for tasks with fd_m_tasks_call_id set
    # (auto-created from a real Call; see services/missed_call_sync.py).
    calls_by_id: dict[UUID, Call] | None = None,
    leads_by_call_id: dict[int, FDLead] | None = None,
    analysis_by_call_id: dict[str, CallAnalysisResult] | None = None,
    clients_by_id: dict[str, Client] | None = None,
    users_by_id: dict[UUID, FDUser] | None = None,
    # Missed Calls page "Called Back" enrichment (see _callback_match). Only
    # ever populated by the batch list path below when a caller explicitly
    # asks for it -- other callers of serialize() are unaffected.
    callback_call: Call | None = None,
    callback_agent_name: str | None = None,
    # Hannah AI needs only this lightweight attended flag to color the
    # original Call ID. It deliberately does not require callback details.
    callback_matched: bool = False,
    # Pre-fetched callback_call.audio_s3_uri/key -> byte size, computed once
    # for every callback call in the batch list path (see
    # _audio_byte_sizes_by_uri) instead of one S3 HEAD request per task
    # here. None (the default for every other caller) falls back to the
    # original single-call lookup below, unchanged.
    audio_bytes_by_uri: dict[str, int] | None = None,
    agent_names: list[str] | None = None,
) -> dict:
    data = {key: (str(value) if isinstance(value, (UUID, date)) else value) for key in FIELDS if (value := getattr(task, f"fd_m_tasks_{key}")) is not None}
    # Derived/read-only classification metadata (Front Desk Agent vs
    # Website -- see services/missed_call_sync.py and
    # services/hannah_ai_routing.py). Deliberately kept out of FIELDS so it
    # can never be set via the task create/update payload; it is computed
    # once at sync time, not something an agent or Team Lead edits.
    if task.fd_m_tasks_hannah_ai_category:
        data["hannah_ai_category"] = task.fd_m_tasks_hannah_ai_category
    if task.task_type == "missed_call" and callback_matched:
        data["callback_matched"] = True
    if db and task.task_type in CALL_TASK_TYPES:
        phone_field = CALL_TASK_PHONE_FIELD[task.task_type]
        phone = data.get(phone_field)
        task_date = _task_relevant_date(task)

        # A task auto-created from a real Call carries an exact link
        # (fd_m_tasks_call_id) -- use it directly instead of re-guessing via
        # phone+date. Every manually imported row has this column NULL and
        # keeps using the existing heuristic below unchanged.
        if task.fd_m_tasks_call_id and calls_by_id is not None:
            call = calls_by_id.get(task.fd_m_tasks_call_id)
        elif task.fd_m_tasks_call_id:
            call = db.query(Call).filter(Call.call_id == task.fd_m_tasks_call_id).first()
        # Try to use pre-loaded calls dict if available (for batch optimization)
        # Otherwise fall back to database query (for single-task requests)
        elif calls_by_phone is not None:
            call = _closest_call(calls_by_phone.get(_task_phone(phone), []), task_date)
        else:
            # No pre-loaded dict, so query database (legacy path)
            call = _match_call_for_task(db, phone, task_date)
        
        if call:
            # Try to use pre-loaded lookups, falling back to database if needed
            if leads_by_call_id and call.call_id in leads_by_call_id:
                lead = leads_by_call_id[call.call_id]
            elif leads_by_call_id is not None:
                lead = None
            else:
                lead = db.query(FDLead).filter(FDLead.fd_a_leads_call_id == call.call_id).first()
            
            if analysis_by_call_id and str(call.call_id) in analysis_by_call_id:
                analysis = analysis_by_call_id[str(call.call_id)]
            elif analysis_by_call_id is not None:
                analysis = None
            else:
                analysis = db.query(CallAnalysisResult).filter(
                    CallAnalysisResult.call_id == str(call.call_id)
                ).first()
            
            voicemail_flag = str(
                analysis.voicemail_left_if_no_answer if analysis else ""
            ).strip().lower() in {"yes", "true", "1"}
            voicemail_outcome = "voicemail" in str(
                analysis.fd_a_call_outcome if analysis else ""
            ).lower()
            data.update({
                "call_id": str(call.call_id),
                "call_reference": call.call_reference or "",
                "client_id": call.client_id or "",
                "lead_id": lead.fd_m_leads_lead_id if lead else "",
                "transcript": call.transcript or "",
                "agent_name_in_call": agent_names_in_transcript(
                    call.transcript,
                    agent_names if agent_names is not None else _agent_names_for_transcripts(db),
                ),
                "audio": "1" if (call.audio_s3_key or call.audio_s3_uri) else "",
                # These values let the Missed/Non-Responsive workspace show
                # duration totals using the same persisted call and voicemail
                # criteria as the Dashboard.
                "billsec": max(int(call.billsec or 0), 0),
                "is_voicemail": voicemail_flag or voicemail_outcome,
            })
            # This page is view-only: Summary, Action Status, and Comments
            # must reflect what's already tracked on the real call record
            # (the same Call Summary, Stage 2 Action Status, and Blocker
            # Comment an agent maintains on the Calls page), not a second,
            # independently typed copy on the task itself. Only overlay a
            # value the call record actually has -- an empty one on the
            # call keeps showing whatever was previously logged on the
            # task rather than blanking it out.
            if analysis:
                if str(analysis.fd_a_call_summary or "").strip():
                    data["summary"] = analysis.fd_a_call_summary
                if str(analysis.fd_m_manual_action_status or "").strip():
                    data["action_status"] = analysis.fd_m_manual_action_status
                if str(analysis.fd_m_blocker_comment or "").strip():
                    data["comments"] = analysis.fd_m_blocker_comment
                # The Missed Calls page's Case Type/Client Type/Lead
                # Source/Email columns reuse the exact same per-call AI
                # analysis fields the Calls page already derives -- not a
                # second, independently typed copy.
                if str(analysis.fd_a_case_type or "").strip():
                    data["case_type"] = analysis.fd_a_case_type
                if str(analysis.fd_a_client_type or "").strip():
                    data["client_type"] = analysis.fd_a_client_type
                if str(analysis.fd_a_lead_source or "").strip():
                    data["lead_source"] = analysis.fd_a_lead_source
                if str(analysis.fd_a_email_address or "").strip():
                    data["email"] = analysis.fd_a_email_address
        # The client ID popup is the source of truth for a client's name;
        # overlay it here so every "name" column matches that popup.
        client_id = data.get("client_id")
        if client_id:
            if clients_by_id and client_id in clients_by_id:
                client = clients_by_id[client_id]
            elif clients_by_id is not None:
                client = None
            else:
                client = db.query(Client).filter(Client.client_id == client_id).first()

            if client and str(client.client_name or "").strip():
                data["name"] = client.client_name
            elif analysis and str(analysis.fd_a_client_name or "").strip():
                # No client profile name set yet -- common for a brand-new
                # caller Hannah AI just took, since nobody has reviewed them
                # yet. Fall back to the AI-extracted name from this call's
                # own transcript instead of leaving Name blank; the profile
                # name above still wins once someone confirms/sets it.
                data["name"] = analysis.fd_a_client_name
            # Same source-of-truth precedence as the name overlay above.
            if client and str(client.client_email or "").strip():
                data["email"] = client.client_email
        if task.task_type == "missed_call" and callback_call is not None:
            cb_call_id = str(callback_call.call_id)
            if analysis_by_call_id and cb_call_id in analysis_by_call_id:
                cb_analysis = analysis_by_call_id[cb_call_id]
            elif analysis_by_call_id is None:
                cb_analysis = db.query(CallAnalysisResult).filter(CallAnalysisResult.call_id == cb_call_id).first()
            else:
                cb_analysis = None
            cb_uri = callback_call.audio_s3_uri or callback_call.audio_s3_key
            cb_has_audio_file = bool(cb_uri)
            if not cb_has_audio_file:
                cb_audio_bytes = None
            elif audio_bytes_by_uri is not None:
                cb_audio_bytes = audio_bytes_by_uri.get(cb_uri)
            else:
                cb_audio_bytes = _audio_byte_size(callback_call)
            cb_voicemail_flag = str(
                cb_analysis.voicemail_left_if_no_answer if cb_analysis else ""
            ).strip().lower() in {"yes", "true", "1"}
            cb_voicemail_outcome = "voicemail" in str(
                cb_analysis.fd_a_call_outcome if cb_analysis else ""
            ).lower()
            data["callback"] = {
                "agent": callback_agent_name or "-",
                "called_back_date": callback_call.call_date.isoformat() if callback_call.call_date else "",
                "transcript": callback_call.transcript or "",
                "audio": "1" if cb_has_audio_file else "",
                "call_id": cb_call_id,
                "call_reference": callback_call.call_reference or "",
                "billsec": max(int(callback_call.billsec or 0), 0),
                "call_summary": cb_analysis.fd_a_call_summary if cb_analysis else "",
                "is_voicemail": cb_voicemail_flag or cb_voicemail_outcome,
                "audio_bytes": cb_audio_bytes,
                # None (S3 lookup failed/unavailable) is treated as "can't
                # tell" rather than "empty" -- see _audio_byte_size.
                "has_real_audio": cb_has_audio_file
                and (cb_audio_bytes is None or cb_audio_bytes >= EMPTY_AUDIO_BYTES_THRESHOLD),
            }

    # Try to use pre-loaded users dict, falling back to database if needed
    if users_by_id and task.assigned_by_id in users_by_id:
        assigned_by = users_by_id[task.assigned_by_id]
    elif users_by_id is not None:
        assigned_by = None
    else:
        assigned_by = db.get(FDUser, task.assigned_by_id) if db and task.assigned_by_id else None
    
    return {"id": str(task.id), "task_type": task.task_type, "data": data, "owner_id": str(task.fd_m_tasks_owner) if task.fd_m_tasks_owner else None, "owner_name": task.owner.name if task.owner else None, "assigned_by_name": assigned_by.name if assigned_by else None, "submitted_at": task.submitted_at.isoformat() if task.submitted_at else None}


def owner_agent(db: Session, raw_id: Any) -> FDUser:
    try:
        owner_id = UUID(str(raw_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Owner must be a valid Front Desk Agent") from exc
    owner = db.get(FDUser, owner_id)
    if not owner or not owner.is_active or normalized_role({"role": owner.role}) != "agent":
        raise HTTPException(422, "Owner must be an active Front Desk Agent")
    return owner


def parse_task_date(value: Any, field: str) -> date:
    raw_value = str(value).strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw_value, pattern).date()
        except ValueError:
            continue
    # Missed-call spreadsheets commonly use grouped labels such as
    # "14th July AM".  The destination is a date-only field, so discard a
    # time-of-day label and infer the current year only when the source did
    # not provide one.
    readable_value = re.sub(r"(\d{1,2})(?:st|nd|rd|th)\b", r"\1", raw_value, flags=re.IGNORECASE)
    readable_value = re.sub(r"\b(?:a\.?m\.?|p\.?m\.?)\b", "", readable_value, flags=re.IGNORECASE)
    readable_value = re.sub(r"\s+", " ", readable_value).strip(" ,")
    for pattern in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(readable_value, pattern).date()
        except ValueError:
            continue
    for pattern in ("%d %B", "%d %b", "%B %d", "%b %d"):
        try:
            return datetime.strptime(readable_value, pattern).replace(year=datetime.now(timezone.utc).year).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    raise HTTPException(422, f"Invalid {field} date")


def apply(task: FDTask, values: dict[str, Any], db: Session, *, validate_owner: bool = False, invalid_fields: list[dict] | None = None, source_row: int | None = None) -> None:
    for key, value in values.items():
        if key not in FIELDS:
            continue
        if key == "owner" and value:
            value = owner_agent(db, value).id if validate_owner else UUID(str(value))
        if key in {"date", "missed_call_date", "miscall_added_date", "date_added"} and value:
            try:
                value = parse_task_date(value, key)
            except HTTPException:
                if invalid_fields is None:
                    raise
                invalid_fields.append({"row": source_row, "field": key, "reason": "Invalid date format"})
                value = None
        setattr(task, f"fd_m_tasks_{key}", value or None)


def require_complete_general_task(task: FDTask) -> None:
    labels = {"fd_m_tasks_date": "Date", "fd_m_tasks_tasks": "Task", "fd_m_tasks_timeline": "Timeline", "fd_m_tasks_owner": "Owner"}
    missing = [label for column, label in labels.items() if not getattr(task, column)]
    if missing:
        raise HTTPException(422, f"General Task requires: {', '.join(missing)}")


def add_notification(task: FDTask, db: Session, *, title: str, message: str, notification_type: str, recipient_id: UUID | None = None) -> None:
    recipient_id = recipient_id or task.fd_m_tasks_owner
    if not recipient_id:
        raise HTTPException(422, "Owner is required before submit")
    db.add(FDTaskNotification(id=uuid4(), task_id=task.id, recipient_id=recipient_id, title=title, message=message, notification_type=notification_type, created_at=datetime.now(timezone.utc)))


def assignment_message(task: FDTask, assigned_by: FDUser) -> str:
    return f"A task has been assigned to you. Assigned date: {task.fd_m_tasks_date.isoformat()}. Task: {task.fd_m_tasks_tasks}. Timeline: {task.fd_m_tasks_timeline}. Assigned by: {assigned_by.name}."


def update_message(task: FDTask, submitted_by: FDUser) -> str:
    return f"Your task was updated. Status: {task.fd_m_tasks_status or '-'}. Comment: {task.fd_m_tasks_comments or '-'}. Updated by: {submitted_by.name}."


def call_assignment_message(task: FDTask, assigned_by: FDUser) -> str:
    if task.task_type == "missed_call":
        task_label = "Missed Call"
        task_date = task.fd_m_tasks_missed_call_date or task.fd_m_tasks_miscall_added_date
        details = [task.fd_m_tasks_name, task.fd_m_tasks_contact_number, task.fd_m_tasks_summary]
    else:
        task_label = "Non-Responsive Call"
        task_date = task.fd_m_tasks_date_added
        details = [task.fd_m_tasks_name, task.fd_m_tasks_phone_number, task.fd_m_tasks_email, task.fd_m_tasks_type]
    relevant = "; ".join(str(value) for value in details if value) or "No additional call information"
    return (
        f"You have been assigned a {task_label} task. Please check your Missed Calls page. "
        f"Task type: {task_label}. Date: {task_date.isoformat() if task_date else '-'}. "
        f"Owner: {task.owner.name if task.owner else '-'}. Assigned by: {assigned_by.name}. "
        f"Call information: {relevant}."
    )


def notify_call_assignment_after_save(task: FDTask, assigned_by_id: UUID, db: Session) -> None:
    """Notify only the call-task owner after its assignment transaction commits."""
    if not task.fd_m_tasks_owner:
        return
    assigned_by = db.get(FDUser, assigned_by_id)
    if not assigned_by:
        raise HTTPException(401, "Assigning user was not found")
    add_notification(
        task,
        db,
        title="Call assigned task",
        message=call_assignment_message(task, assigned_by),
        notification_type=f"{task.task_type}_assignment",
    )
    db.commit()


@router.get("/owners")
def get_general_task_owners(user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not can_edit(user, db):
        raise HTTPException(403, "Task editor access required")
    agents = db.query(FDUser).filter(FDUser.is_active.is_(True), func.lower(func.trim(FDUser.role)) == "agent").order_by(FDUser.name).all()
    return [{"id": str(agent.id), "name": agent.name, "email": agent.email} for agent in agents]


NOTIFICATION_FILTERS = ("all", "unread", "read")


def _own_notifications(db: Session, user: dict):
    return db.query(FDTaskNotification).filter(FDTaskNotification.recipient_id == current_fd_user_id(user))


@router.get("/notifications")
def get_my_notifications(status: str = "all", user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The caller's own notifications, newest first. `status` = all | unread | read (default all)."""
    status = (status or "all").strip().lower()
    if status not in NOTIFICATION_FILTERS:
        raise HTTPException(422, "status must be all, unread or read")
    query = _own_notifications(db, user)
    if status == "unread":
        query = query.filter(FDTaskNotification.read_at.is_(None))
    elif status == "read":
        query = query.filter(FDTaskNotification.read_at.isnot(None))
    rows = query.order_by(FDTaskNotification.created_at.desc()).limit(50).all()
    return [{
        "id": str(item.id),
        "task_id": str(item.task_id) if item.task_id else None,
        "lead_id": str(item.lead_id) if item.lead_id else None,
        "entity_id": item.entity_id,
        "source_page": item.source_page,
        "change_context": item.change_context or {},
        "title": item.title,
        "message": item.message,
        "notification_type": item.notification_type,
        "created_at": item.created_at.isoformat(),
        "read_at": item.read_at.isoformat() if item.read_at else None,
    } for item in rows]


@router.get("/notifications/summary")
def get_my_notification_summary(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Exact unread/total counts for the caller (the list itself is capped at 50 rows)."""
    unread, total = _own_notifications(db, user).with_entities(
        func.count().filter(FDTaskNotification.read_at.is_(None)), func.count()).one()
    return {"unread": int(unread or 0), "total": int(total or 0)}


@router.patch("/notifications/read-all")
def mark_all_notifications_read(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Mark every unread notification of the caller (and only the caller) as read."""
    updated = _own_notifications(db, user).filter(FDTaskNotification.read_at.is_(None)).update(
        {FDTaskNotification.read_at: datetime.now(timezone.utc)}, synchronize_session=False)
    db.commit()
    return {"updated": int(updated)}


@router.patch("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    notification = db.query(FDTaskNotification).filter(FDTaskNotification.id == notification_id, FDTaskNotification.recipient_id == current_fd_user_id(user)).first()
    if not notification:
        raise HTTPException(404, "Notification not found")
    notification.read_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": str(notification.id), "read_at": notification.read_at.isoformat()}


@router.get("")
def get(start_date: date | None = None, end_date: date | None = None, agent: str = "", task_type: str = "", include_callback: bool = False, include_callback_status: bool = False, page: int | None = None, page_size: int | None = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    # ``serialize`` reads task.owner. Eager loading keeps that display-only
    # relationship from producing a query per row during page rendering.
    query = db.query(FDTask).options(joinedload(FDTask.owner)).order_by(FDTask.created_at.desc())
    if task_type:
        # Optional, additive filter: existing callers (Tasks.jsx,
        # UnansweredCalls.jsx) fetch every task_type and filter client-side,
        # and keep doing so unaffected. The dedicated Missed Calls page
        # requests only "missed_call" server-side to avoid pulling every
        # task_type's rows just to discard most of them.
        query = query.filter(FDTask.task_type == task_type)
    # Every signed-in user sees every task of every type (General, Non Responsive, Missed Call). Agents cannot change any of them:
    # every mutation below stays gated on can_edit.
    if agent:
        # The shared Agent filter: only the tasks owned by that agent (a filter over data everyone already sees).
        try:
            query = query.filter(FDTask.fd_m_tasks_owner == UUID(agent))
        except ValueError as exc:
            raise HTTPException(422, "agent must be a valid user ID") from exc
    if start_date:
        query = query.filter(or_(FDTask.fd_m_tasks_date >= start_date, FDTask.fd_m_tasks_date_added >= start_date, FDTask.fd_m_tasks_missed_call_date >= start_date, FDTask.fd_m_tasks_miscall_added_date >= start_date))
    if end_date:
        query = query.filter(or_(FDTask.fd_m_tasks_date <= end_date, FDTask.fd_m_tasks_date_added <= end_date, FDTask.fd_m_tasks_missed_call_date <= end_date, FDTask.fd_m_tasks_miscall_added_date <= end_date))
    if page is not None and page_size is not None:
        page = max(page, 1); page_size = min(max(page_size, 1), 500)
        query = query.offset((page - 1) * page_size).limit(page_size)
    rows = query.all()

    def included_by_date(task: FDTask) -> bool:
        # Missed Call tasks are frequently created with only their "added"
        # date populated (missed_call_date itself is often left blank), so
        # that field must be checked too or every Missed Call row silently
        # fails a date-range filter that only checks the other three.
        task_date = (
            task.fd_m_tasks_date
            or task.fd_m_tasks_date_added
            or task.fd_m_tasks_missed_call_date
            or task.fd_m_tasks_miscall_added_date
        )
        return (not start_date or (task_date or date.min) >= start_date) and (not end_date or (task_date or date.max) <= end_date)

    # OPTIMIZATION: Batch load all related data instead of N+1 queries per task
    # Extract phone numbers from tasks that need call lookups
    tasks_to_serialize = [task for task in rows if included_by_date(task) and (not agent or str(task.fd_m_tasks_owner) == agent)]
    
    # Collect all phones that need matching
    phones_to_match = set()
    for task in tasks_to_serialize:
        if task.task_type in CALL_TASK_TYPES:
            phone_field = CALL_TASK_PHONE_FIELD[task.task_type]
            phone_value = getattr(task, f"fd_m_tasks_{phone_field}", None)
            if phone_value:
                phone_to_match = _task_phone(phone_value)
                if phone_to_match:
                    phones_to_match.add(phone_to_match)
    
    # Batch load every call from each phone number that needs matching, so
    # each task can pick the one nearest its own date (see _closest_call)
    # instead of whichever call from that number happens to be newest
    # overall. Filtered in SQL to just these specific phone numbers --
    # fetching the entire calls table here (as this used to) held a DB
    # connection for the whole scan on every single Missed Calls/Tasks page
    # load, which was exhausting the connection pool for the whole app.
    calls_by_phone: dict[str, list[Call]] = {}
    if phones_to_match:
        phone_match_clause = or_(*(
            or_(Call.src.like(f"%{digits}"), Call.dst.like(f"%{digits}"))
            for digits in phones_to_match
        ))
        matching_calls = db.query(Call).filter(phone_match_clause).all()
        for call in matching_calls:
            src_digits = _task_phone(call.src)
            dst_digits = _task_phone(call.dst)
            for digits in (src_digits, dst_digits):
                if digits in phones_to_match:
                    calls_by_phone.setdefault(digits, []).append(call)

    # Tasks auto-created from a real Call (fd_m_tasks_call_id set -- see
    # services/missed_call_sync.py) are matched to that exact call rather
    # than guessed by phone+date.
    direct_call_ids = {task.fd_m_tasks_call_id for task in tasks_to_serialize if task.fd_m_tasks_call_id}
    calls_by_id: dict[UUID, Call] = {}
    if direct_call_ids:
        calls_by_id = {call.call_id: call for call in db.query(Call).filter(Call.call_id.in_(direct_call_ids)).all()}

    # Missed Calls page "Called Back" view: for each missed_call task, look
    # for an outbound call on the exact same date among the calls already
    # fetched for that phone number above (calls_by_phone) -- no extra query
    # is needed since that batch already holds every call from that number,
    # both directions.
    callback_call_by_task_id: dict[UUID, Call] = {}
    if include_callback or include_callback_status:
        system_numbers = _system_numbers(db)
        for task in tasks_to_serialize:
            if task.task_type != "missed_call":
                continue
            phone_digits = _task_phone(getattr(task, "fd_m_tasks_contact_number", None))
            match = _callback_match(calls_by_phone.get(phone_digits, []), _task_relevant_date(task), system_numbers)
            if match:
                callback_call_by_task_id[task.id] = match
    callback_agent_name_by_task_id: dict[UUID, str] = {}
    if include_callback and callback_call_by_task_id:
        names = _agent_names_with_manual_override(db, list(callback_call_by_task_id.values()))
        callback_agent_name_by_task_id = {
            task_id: names.get(str(call.call_id), "-") for task_id, call in callback_call_by_task_id.items()
        }
    # Collect all call IDs we found
    call_ids = {call.call_id for calls in calls_by_phone.values() for call in calls} | set(calls_by_id.keys())
    
    # Batch load FDLeads for these calls
    leads_by_call_id = {}
    if call_ids:
        leads = db.query(FDLead).filter(FDLead.fd_a_leads_call_id.in_(call_ids)).all()
        leads_by_call_id = {lead.fd_a_leads_call_id: lead for lead in leads}
    
    # Batch load CallAnalysisResults for these calls
    analysis_by_call_id = {}
    if call_ids:
        analyses = db.query(CallAnalysisResult).filter(CallAnalysisResult.call_id.in_([str(cid) for cid in call_ids])).all()
        # CallAnalysisResult.call_id is stored as uuid in the database despite
        # the ORM's Text declaration, so it comes back as a UUID object here
        # -- str() it so this matches the str(call.call_id) lookup below
        # (the same workaround used elsewhere in this codebase for this
        # column). Without it, analysis was never found via this batch path,
        # silently leaving voicemail detection, Summary, and Action Status
        # blank for every task even when matched to a call.
        analysis_by_call_id = {str(analysis.call_id): analysis for analysis in analyses}
    
    # Collect all client IDs we'll need
    client_ids = set()
    for calls in calls_by_phone.values():
        for call in calls:
            if call.client_id:
                client_ids.add(call.client_id)
    for call in calls_by_id.values():
        if call.client_id:
            client_ids.add(call.client_id)

    # Batch load Clients for these IDs
    clients_by_id = {}
    if client_ids:
        clients = db.query(Client).filter(Client.client_id.in_(client_ids)).all()
        clients_by_id = {client.client_id: client for client in clients}
    
    # Collect all user IDs (owners and assigned_by)
    user_ids = set()
    for task in tasks_to_serialize:
        if task.fd_m_tasks_owner:
            user_ids.add(task.fd_m_tasks_owner)
        if task.assigned_by_id:
            user_ids.add(task.assigned_by_id)
    # Also add the owner relationship (task.owner_id relationship)
    for task in tasks_to_serialize:
        if hasattr(task, 'owner') and task.owner and task.owner.id:
            user_ids.add(task.owner.id)
    
    # Batch load FDUsers for these IDs
    users_by_id = {}
    if user_ids:
        users = db.query(FDUser).filter(FDUser.id.in_(user_ids)).all()
        users_by_id = {user.id: user for user in users}
    
    # Finish all database-backed serialization before doing S3 I/O.  A
    # request-scoped SQLAlchemy session keeps its checked-out connection until
    # it is closed; running remote HEAD requests first let a slow S3 response
    # pin the connection and starve unrelated Calls/Leads/Dashboard requests.
    # An empty mapping intentionally prevents serialize() from falling back
    # to an individual S3 lookup while the database session is still active.
    agent_names = _agent_names_for_transcripts(db)
    serialized = [
        serialize(
            task,
            db,
            calls_by_phone=calls_by_phone,
            calls_by_id=calls_by_id,
            leads_by_call_id=leads_by_call_id,
            analysis_by_call_id=analysis_by_call_id,
            clients_by_id=clients_by_id,
            users_by_id=users_by_id,
            callback_call=callback_call_by_task_id.get(task.id) if include_callback else None,
            callback_agent_name=callback_agent_name_by_task_id.get(task.id) if include_callback else None,
            callback_matched=task.id in callback_call_by_task_id,
            audio_bytes_by_uri={},
            agent_names=agent_names,
        )
        for task in tasks_to_serialize
    ]

    if not include_callback or not callback_call_by_task_id:
        return serialized

    callback_uri_by_call_id = {
        str(call.call_id): (call.audio_s3_uri or call.audio_s3_key)
        for call in callback_call_by_task_id.values()
    }
    # All ORM fields required above are eagerly/batch loaded. Release this
    # request's database connection before the concurrent network checks.
    db.close()
    audio_bytes_by_uri = _audio_byte_sizes_by_uri(list(callback_call_by_task_id.values()))
    for item in serialized:
        callback = item.get("data", {}).get("callback")
        if not callback:
            continue
        uri = callback_uri_by_call_id.get(callback.get("call_id"))
        if not uri:
            continue
        audio_bytes = audio_bytes_by_uri.get(uri)
        callback["audio_bytes"] = audio_bytes
        callback["has_real_audio"] = (
            audio_bytes is None or audio_bytes >= EMPTY_AUDIO_BYTES_THRESHOLD
        )
    return serialized


@router.get("/dashboard-summary")
def dashboard_summary(start_date: date, end_date: date, agent: str = "", user=Depends(get_current_user), db: Session = Depends(get_db)):
    if end_date < start_date:
        raise HTTPException(422, "end_date must be on or after start_date")
    done = {"called back", "done", "completed", "resolved", "closed"}
    called = case((func.lower(func.trim(FDTask.fd_m_tasks_action_status)).in_(done), 1), else_=0)
    def scope(query): return query.join(FDUser, FDUser.id == FDTask.fd_m_tasks_owner).filter(func.lower(FDUser.name) == agent.strip().lower()) if agent else query

    # Missed Call "called back" is determined the same way the Missed Calls
    # page's own Called Back view determines it -- a matching outbound call
    # by phone + date (_callback_match) -- not fd_m_tasks_action_status.
    # That field has no editable UI for missed_call tasks any more (its only
    # surface was the old Missed Calls tab on the Non-Responsive Calls page,
    # since removed in favor of the dedicated Missed Calls page), so it
    # stays permanently blank and would silently report zero callbacks
    # regardless of how many actually happened.
    missed_call_tasks = scope(db.query(FDTask)).filter(
        FDTask.task_type == "missed_call",
        FDTask.fd_m_tasks_missed_call_date.between(start_date, end_date),
    ).all()
    system_numbers = _system_numbers(db)
    phones_to_match = {phone for task in missed_call_tasks if (phone := _task_phone(task.fd_m_tasks_contact_number))}
    # Same "load every call, bucket by phone digits" approach the Missed
    # Calls list endpoint (see get() below) already uses for this exact
    # matching -- reused here rather than a second lookup strategy.
    calls_by_phone: dict[str, list[Call]] = {}
    if phones_to_match:
        phone_match_clause = or_(*(
            or_(Call.src.like(f"%{digits}"), Call.dst.like(f"%{digits}"))
            for digits in phones_to_match
        ))
        for call in db.query(Call).filter(phone_match_clause).all():
            for digits in (_task_phone(call.src), _task_phone(call.dst)):
                if digits in phones_to_match:
                    calls_by_phone.setdefault(digits, []).append(call)
    missed_by_date: dict[date, dict] = {}
    for task in missed_call_tasks:
        task_date = task.fd_m_tasks_missed_call_date
        bucket = missed_by_date.setdefault(task_date, {"count": 0, "called_back": 0, "added_date": None})
        bucket["count"] += 1
        if task.fd_m_tasks_miscall_added_date and (bucket["added_date"] is None or task.fd_m_tasks_miscall_added_date < bucket["added_date"]):
            bucket["added_date"] = task.fd_m_tasks_miscall_added_date
        phone = _task_phone(task.fd_m_tasks_contact_number)
        if _callback_match(calls_by_phone.get(phone, []), _task_relevant_date(task), system_numbers):
            bucket["called_back"] += 1

    non = scope(db.query(FDTask.fd_m_tasks_date_added.label("date"), func.count(FDTask.id).label("count"), func.sum(called).label("called_back"))).filter(FDTask.task_type == "non_responsiveness", FDTask.fd_m_tasks_date_added.between(start_date, end_date)).group_by(FDTask.fd_m_tasks_date_added).all()
    non_by_date = {row.date: row for row in non}; missed_rows = []; non_rows = []; current = start_date
    while current <= end_date:
        missed_bucket = missed_by_date.get(current); non_row = non_by_date.get(current)
        missed_rows.append({"date": current.isoformat(), "added_date": missed_bucket["added_date"].isoformat() if missed_bucket and missed_bucket["added_date"] else None, "count": missed_bucket["count"] if missed_bucket else 0, "called_back": missed_bucket["called_back"] if missed_bucket else 0})
        non_rows.append({"date": current.isoformat(), "count": int(non_row.count or 0) if non_row else 0, "called_back": int(non_row.called_back or 0) if non_row else 0})
        current = current.fromordinal(current.toordinal() + 1)
    return {"missed_calls": missed_rows, "non_responsive_calls": non_rows, "totals": {"missed_calls": sum(row["count"] for row in missed_rows), "missed_called_back": sum(row["called_back"] for row in missed_rows), "non_responsive_calls": sum(row["count"] for row in non_rows), "non_responsive_called_back": sum(row["called_back"] for row in non_rows)}}


@router.post("")
def create(payload: Payload, user=Depends(get_current_user), db: Session = Depends(get_db)):
    is_general = payload.task_type == "general"
    is_call_task = payload.task_type in CALL_TASK_TYPES
    is_editor = can_edit(user, db)
    # Tasks are view-only for agents; creating one needs editor access.
    if not is_editor:
        raise HTTPException(403, "Task editor access required")
    task = FDTask(id=uuid4(), task_type=payload.task_type, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    apply(task, payload.data, db, validate_owner=is_general or is_call_task)
    if is_general:
        task.assigned_by_id = current_fd_user_id(user)
        if payload.submit:
            require_complete_general_task(task)
    db.add(task); db.flush()
    if is_general and payload.submit:
        assigned_by = db.get(FDUser, task.assigned_by_id)
        add_notification(task, db, title="Task assigned to you", message=assignment_message(task, assigned_by), notification_type="general_task_assignment")
        task.submitted_at = datetime.now(timezone.utc)
    db.commit()
    if is_call_task and payload.submit and task.fd_m_tasks_owner:
        task.assigned_by_id = current_fd_user_id(user)
        db.commit()
        notify_call_assignment_after_save(task, task.assigned_by_id, db)
    return serialize(task, db)


@router.patch("/{task_id}")
def update(task_id: UUID, payload: Payload, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not can_edit(user, db):
        raise HTTPException(403, "Task editor access required")
    task = db.get(FDTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    old_owner, old_status, old_comments = task.fd_m_tasks_owner, task.fd_m_tasks_status, task.fd_m_tasks_comments
    is_call_task = task.task_type in CALL_TASK_TYPES
    apply(task, payload.data, db, validate_owner=task.task_type == "general" or is_call_task)
    task.updated_at = datetime.now(timezone.utc)
    if task.task_type == "general" and payload.submit:
        require_complete_general_task(task)
        submitted_by = db.get(FDUser, current_fd_user_id(user))
        if not task.submitted_at or task.fd_m_tasks_owner != old_owner:
            task.assigned_by_id = submitted_by.id
            add_notification(task, db, title="Task assigned to you", message=assignment_message(task, submitted_by), notification_type="general_task_assignment")
        elif task.fd_m_tasks_status != old_status or task.fd_m_tasks_comments != old_comments:
            add_notification(task, db, title="Task updated", message=update_message(task, submitted_by), notification_type="general_task_update")
        task.submitted_at = datetime.now(timezone.utc)
    db.commit()
    if is_call_task and payload.submit and task.fd_m_tasks_owner and task.fd_m_tasks_owner != old_owner:
        task.assigned_by_id = current_fd_user_id(user)
        db.commit()
        notify_call_assignment_after_save(task, task.assigned_by_id, db)
    return serialize(task, db)


@router.post("/import")
def bulk(payload: Bulk, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not can_edit(user, db):
        raise HTTPException(403, "Task editor access required")
    if payload.task_type not in CALL_TASK_TYPES:
        raise HTTPException(422, "Import is available only for Missed Call and Non-Responsive Call tasks")
    # Partial imports are valid: a sheet may contain only Name, only Email,
    # or any other single supported task column.  It must, however, include
    # at least one supported column so arbitrary uploads cannot create empty
    # task records.
    if not any(any(key in FIELDS for key in record) for record in payload.records):
        raise HTTPException(422, "Import must include at least one recognized task column.")
    made = []; invalid_fields = []; skipped_empty = 0
    current_missed_call_date: date | None = None
    for row_number, data in enumerate(payload.records, start=2):
        if not any(str(value or "").strip() for key, value in data.items() if not key.startswith("__")):
            skipped_empty += 1
            continue
        task = FDTask(id=uuid4(), task_type=payload.task_type, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        values = dict(data)
        if payload.task_type == "missed_call":
            # Email Date labels can be written once for a group in the source
            # sheet.  Carry the most recently *valid* value forward only for
            # Missed Call Date; each row's Date remains independently parsed.
            source_email_date = values.get("missed_call_date")
            if str(source_email_date or "").strip():
                try:
                    current_missed_call_date = parse_task_date(source_email_date, "missed_call_date")
                    values["missed_call_date"] = current_missed_call_date
                except HTTPException:
                    invalid_fields.append({"row": row_number, "field": "missed_call_date", "reason": "Invalid date format"})
                    values.pop("missed_call_date", None)
            elif current_missed_call_date is not None:
                values["missed_call_date"] = current_missed_call_date
            else:
                values.pop("missed_call_date", None)
        # Imported call tasks always start unassigned. Source columns such as
        # Missed Call's "Contacted by" are source data, not a task owner; an
        # editor assigns an owner directly in that row's Owner column later.
        apply(task, {key: value for key, value in values.items() if key != "owner"}, db, invalid_fields=invalid_fields, source_row=row_number)
        db.add(task); made.append(task)
    db.commit()
    for warning in invalid_fields:
        warning["task_type"] = payload.task_type
    return {"inserted": len(made), "invalid_fields": invalid_fields, "skipped_empty": skipped_empty}


@router.post("/assign")
def assign_bulk(payload: BulkAssign, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Assign only selected call tasks to one validated Front Desk Agent."""
    if not can_edit(user, db):
        raise HTTPException(403, "Task editor access required")
    if payload.task_type not in CALL_TASK_TYPES:
        raise HTTPException(422, "Assignment is available only for call tasks")
    ids = list(dict.fromkeys(payload.ids))
    if not ids:
        return {"assigned": 0, "notified": 0}
    owner = owner_agent(db, payload.owner_id)
    assigned_by_id = current_fd_user_id(user)
    rows = db.query(FDTask).filter(
        FDTask.id.in_(ids),
        FDTask.task_type == payload.task_type,
    ).all()
    # Never report a partial bulk success. The UI either assigns every
    # explicitly selected task or leaves all of them unchanged with a clear
    # error, preventing a mixed/unknown owner state after stale selection.
    if len(rows) != len(ids):
        raise HTTPException(404, "One or more selected call tasks were not found.")

    # One call occurrence, one agent. The old phone-only guard incorrectly
    # treated a later call from the same client as the old call, preventing
    # today's task from being assigned. Compare exact source IDs when
    # available and otherwise caller number + business date for legacy rows.
    conflict_owner_by_occurrence: dict[str, str] = {}
    if rows:
        other_owned = db.query(FDTask).filter(
            FDTask.task_type.in_(CALL_TASK_TYPES),
            FDTask.fd_m_tasks_owner.isnot(None),
            FDTask.fd_m_tasks_owner != owner.id,
            FDTask.id.notin_(ids),
        ).all()
        for task in rows:
            for other in other_owned:
                if not _same_call_occurrence(task, other):
                    continue
                digits = _task_phone(getattr(task, f"fd_m_tasks_{CALL_TASK_PHONE_FIELD[task.task_type]}", None))
                key = digits or str(getattr(task, "fd_m_tasks_call_id", "this call"))
                if key not in conflict_owner_by_occurrence:
                    other_owner = db.get(FDUser, other.fd_m_tasks_owner)
                    conflict_owner_by_occurrence[key] = other_owner.name if other_owner else "another agent"
    if conflict_owner_by_occurrence:
        detail = "; ".join(f"{number} is already assigned to {name}" for number, name in conflict_owner_by_occurrence.items())
        raise HTTPException(409, f"One call can only be assigned to one agent: {detail}.")

    changed = []
    reassigned = []
    for task in rows:
        # A Missed Call import can carry a source "Contacted by" owner.  That
        # is only source data, not a completed assignment.  The first final
        # submission must still notify the selected agent even if that source
        # owner happens to be the same person.
        previous_owner_id = task.fd_m_tasks_owner
        if previous_owner_id != owner.id or task.assigned_by_id is None:
            changed.append(task)
        if task.submitted_at and previous_owner_id and previous_owner_id != owner.id:
            reassigned.append((task, previous_owner_id))
        task.fd_m_tasks_owner = owner.id
        task.assigned_by_id = assigned_by_id
        task.updated_at = datetime.now(timezone.utc)
        task.submitted_at = datetime.now(timezone.utc)
    # Assignment is durable before an owner notification is added.
    db.commit()
    assigned_by = db.get(FDUser, assigned_by_id)
    for task in changed:
        add_notification(
            task,
            db,
            title="Call assigned task",
            message=call_assignment_message(task, assigned_by),
            notification_type=f"{task.task_type}_assignment",
        )
    for task, previous_owner_id in reassigned:
        add_notification(
            task,
            db,
            title="Call task reassigned",
            message=(
                "This call task has been reassigned to another agent and is no longer assigned to you. "
                f"Reassigned by: {assigned_by.name}."
            ),
            notification_type=f"{task.task_type}_reassigned",
            recipient_id=previous_owner_id,
        )
    if changed:
        db.commit()
    return {"assigned": len(rows), "notified": len(changed)}


@router.delete("/bulk")
def delete_bulk(payload: BulkDelete, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not can_edit(user, db):
        raise HTTPException(403, "Task editor access required")
    permitted_task_types = {"general", *CALL_TASK_TYPES}
    if payload.task_type not in permitted_task_types:
        raise HTTPException(422, "Unsupported task type for deletion")
    ids = list(dict.fromkeys(payload.ids))
    if not ids:
        return {"deleted": 0}
    rows = db.query(FDTask).filter(
        FDTask.id.in_(ids),
        FDTask.task_type == payload.task_type,
    ).all()
    for task in rows:
        db.delete(task)
    db.commit()
    return {"deleted": len(rows)}
