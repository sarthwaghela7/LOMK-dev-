from __future__ import annotations

import re
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, load_only

from middleware.auth import get_current_user
from storage.db import get_db
from storage.models import Call, CallAnalysisResult, FDAssignment, FDLead, FDTask, FDTaskNotification, FDSyncFilter, FDUser
from routes.calls import _agent_names_with_manual_override
from services.client_lifecycle import normalize_phone_number

from services.lead_read_query import filtered_leads, lead_id_order

router = APIRouter(prefix="/api/flintdesk/leads", tags=["FlintDesk Leads"])
logger = logging.getLogger(__name__)
CENTRAL_TIMEZONE = ZoneInfo("America/Chicago")
LEAD_CALL_COLUMNS = (
    Call.call_id, Call.client_id, Call.call_date, Call.call_time, Call.src,
    Call.dst, Call.extension, Call.extname, Call.billsec, Call.call_reference,
    Call.audio_s3_uri, Call.audio_s3_key,
)

S1_FIELDS = {"phone", "sid", "email", "client_type", "client_profile", "client_enquiry", "lead_category", "s1_status", "processed", "flag", "lead_id", "owner", "date_received", "source", "name", "extension", "needs"}
S2_FIELDS = {"agent_name", "call_direction", "call_duration", "case_type", "case_worker_assigned", "urgency_flag", "redirect_escalation_to", "call_outcome", "call_summary", "enquiry_question", "s2_status"}
# Stage 3 ("Leads Action Tracking") was removed from the Leads page; its
# field set, FIXED_VALUES entries, and ANALYSIS_FIELD_MAP entries were
# removed with it. See migrations/050_drop_leads_stage3_columns.py.
ALL_FIELDS = S1_FIELDS | S2_FIELDS
FIXED_VALUES = {
    # "source" is free text: it holds the sheet's UTM source as received.
    "lead_category": {"EB Employment / DC", "Family GC", "General Query", "RFE / Case Rescue", "Student", "Visitor / B1,B2", "Work Visa"},
    "s1_status": {"New", "Moved Over"},
    "s2_status": {"Contacted", "Dropped", "Initial email sent", "New", "Non Responsive", "On hold", "PC Completed"},
}
SOURCE_IMPORT_ALIASES = {
    "website": "Websites",
    "websites": "Websites",
    "organic website": "Websites",
    "organic (website)": "Websites",
}
ANALYSIS_FIELD_MAP = {
    "agent_name": "front_desk_person_name", "call_direction": "call_type", "call_duration": "call_duration",
    "case_type": "fd_a_case_type", "case_worker_assigned": "fd_a_case_worker_assigned",
    "urgency_flag": "fd_a_urgency_flag", "redirect_escalation_to": "escalation_to",
    "call_outcome": "fd_a_call_outcome", "call_summary": "fd_a_call_summary",
    "enquiry_question": "enquiry_details",
}


class LeadImport(BaseModel):
    records: list[dict[str, Any]] = Field(min_length=1, max_length=5000)


class LeadPatch(BaseModel):
    field: str
    value: Any = None


class LeadBulkDelete(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=5000)
    # Owner choices are deliberately carried only with Submit.  Choosing an
    # owner in the manager's table is a local draft until this request
    # succeeds, so an agent can never see a lead before it is submitted.
    owner_ids: dict[UUID, UUID] = Field(default_factory=dict)


def _role(user):
    """Normalize the role spelling carried by legacy and current JWTs."""
    role = re.sub(r"[\s-]+", "_", str(user.get("role", "agent")).strip().lower())
    return {
        "teamlead": "team_lead",
        "subjectmatterexpert": "subject_matter_expert",
        "sme": "subject_matter_expert",
    }.get(role, role)


def _effective_role(user: dict, db: Session | None = None) -> str:
    """Use the active ``fd_users`` record when possible.

    A token can legitimately outlive a role update, or originate from an
    earlier role spelling.  Leads authorization must use the canonical,
    active FlintDesk account rather than rejecting a real Admin because of a
    stale token claim.
    """
    if db is not None:
        try:
            user_id = UUID(str(user.get("fd_user_id")))
            persisted_user = db.get(FDUser, user_id)
            if persisted_user and persisted_user.is_active:
                return _role({"role": persisted_user.role})
        except (TypeError, ValueError):
            pass
    return _role(user)


def _is_admin(user, db: Session | None = None):
    return _effective_role(user, db) in {"admin", "superuser", "super_user"}


def _can_manage_leads(user, db: Session | None = None):
    return _effective_role(user, db) in {"admin", "team_lead", "subject_matter_expert", "superuser", "super_user"}


def _require_manager(user, db: Session | None = None):
    if not _can_manage_leads(user, db):
        raise HTTPException(status_code=403, detail="Lead management requires admin, team lead, or SME access.")


def _owner_agent(db: Session, value: Any) -> FDUser:
    """Resolve an owner from its canonical ``fd_users.id``.

    The name fallback exists only for already-persisted legacy leads, so they
    can be safely submitted once and upgraded to an id-backed assignment.
    New imports and edits always send an id from the Owner dropdown.
    """
    raw_owner = str(value or "").strip()
    if not raw_owner:
        raise HTTPException(status_code=422, detail="Choose an Owner before assigning a lead.")
    try:
        owner = db.get(FDUser, UUID(raw_owner))
    except ValueError:
        owner = next((candidate for candidate in db.query(FDUser).filter(FDUser.is_active.is_(True)).all()
                      if candidate.name.strip().lower() == raw_owner.lower()), None)
    if not owner or _role({"role": owner.role}) != "agent":
        raise HTTPException(status_code=422, detail="The selected Agent could not be found.")
    return owner


def _current_fd_user_id(user: dict) -> UUID:
    try:
        return UUID(str(user["fd_user_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="FlintDesk user identity is required.") from exc


def _set_owner(record: dict, owner: FDUser) -> None:
    record["owner"] = owner.name
    record["owner_id"] = owner.id


def _notify_lead_assignment(row: FDLead, owner: FDUser, db: Session) -> None:
    """Persist a notification for a newly changed lead assignment.

    Callers invoke this only after detecting an owner-id change, so retries of
    an unchanged import do not create another alert while true reassignment
    back to a prior owner still does.
    """
    description = row.fd_m_leads_name or row.fd_m_leads_phone or row.fd_m_leads_lead_id
    db.add(FDTaskNotification(
        id=uuid4(), lead_id=row.id, recipient_id=owner.id,
        title="Lead assigned to you",
        message=f"A new lead has been assigned to you: {description}. Please check your Leads page.",
        notification_type="lead_assignment", created_at=datetime.now(timezone.utc),
    ))


def _lead_owner_has_been_notified(row: FDLead, owner: FDUser, db: Session) -> bool:
    """Whether this owner has already received this lead's assignment alert."""
    return db.query(FDTaskNotification.id).filter(
        FDTaskNotification.lead_id == row.id,
        FDTaskNotification.recipient_id == owner.id,
        FDTaskNotification.notification_type == "lead_assignment",
    ).first() is not None


def _assigned_extensions(db: Session, user: dict) -> set[str] | None:
    # Lead managers assign existing Lead records by Lead ID.  Their ability
    # to manage Leads must not depend on a Call-routing extension.
    if _can_manage_leads(user, db):
        return None
    try:
        user_id = UUID(str(user.get("fd_user_id")))
    except (TypeError, ValueError):
        return set()
    return {str(value).strip().lower() for (value,) in db.query(FDAssignment.extension).filter(
        FDAssignment.agent_id == user_id,
        FDAssignment.is_active.is_(True),
    ).all() if value}


def _phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else digits


def _normalise_source(value):
    text = str(value or "").strip()
    return SOURCE_IMPORT_ALIASES.get(text.lower(), text)


def _lead_received_date(value):
    """Parse canonical ISO or explicit US MM/DD/YYYY Lead dates."""
    raw = str(value or "").strip()
    if not raw:
        return None
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw[:10], pattern).date()
        except ValueError:
            continue
    return None


def _call_phone(call: Call):
    # Inbound callers are normally src; outbound clients are normally dst.
    # Include the canonical Client ID phone so Analyse covers both call-side
    # and client-side phone fields even when one call leg is a system number.
    client_phone = call.client.phone_number if call.client else None
    return {_phone(call.src), _phone(call.dst), _phone(client_phone)} - {""}


def _configured_call_numbers(db: Session):
    return {_phone(value) for (value,) in db.query(FDSyncFilter.value).filter(FDSyncFilter.filter_type == "number").all() if _phone(value)}


def _eligible_outbound_calls(db: Session, allowed: set[str] | None, phone: str):
    """Use the Calls routing definition: configured source -> remote destination."""
    normalized_phone = _phone(phone)
    if not normalized_phone:
        # No valid phone to match against -- a full-table scan can never
        # usefully answer this, and one lead with malformed phone data must
        # not fall through to the same unbounded scan this function exists
        # to avoid (see _scoped_calls's docstring/comment).
        return []
    system_numbers = _configured_call_numbers(db)
    return [call for call in _scoped_calls(db, allowed, phone=normalized_phone)
            if _phone(call.dst) == normalized_phone
            and _phone(call.src) in system_numbers
            and (allowed is None or str(call.extension or "").strip().lower() in allowed)]


def _eligible_calls(db: Session, allowed: set[str] | None, phone: str, lead_row: FDLead | None = None):
    """Return newest inbound or outbound calls matching the lead phone."""
    normalized_phone = _phone(phone)
    if not normalized_phone:
        return []
    candidates = [call for call in _scoped_calls(db, allowed, phone=normalized_phone)
                  if normalized_phone in {_phone(call.src), _phone(call.dst)}]
    if not candidates:
        return []
    candidate_ids = [call.call_id for call in candidates]
    occupied = {
        linked.fd_a_leads_call_id
        for linked in db.query(FDLead).filter(FDLead.fd_a_leads_call_id.in_(candidate_ids)).all()
        if lead_row is None or linked.id != lead_row.id
    }
    return [call for call in candidates if call.call_id not in occupied]


def _time_to_seconds(value) -> int:
    if not value:
        return 0
    return value.hour * 3600 + value.minute * 60 + value.second


def _closest_call_by_date(calls: list[Call], target_date: date | None) -> Call | None:
    """Pick the call whose date is nearest target_date.

    Same nearest-date tie-break as routes/flintdesk/tasks.py's _closest_call
    (used there for Missed Call identification) -- reused here rather than a
    second, separate heuristic, so a Lead identified by date+phone can't
    accidentally land on a call from a completely different date just
    because it's the newest one ever made from that number.
    """
    if not calls:
        return None
    if not target_date:
        return max(calls, key=lambda c: (c.call_date, c.call_time))
    return min(calls, key=lambda c: (abs((c.call_date - target_date).days), -_time_to_seconds(c.call_time)))


def _identified_calls_by_lead(db: Session, rows: list[FDLead]) -> dict[UUID, Call]:
    """Best-effort "which call does this Lead correspond to" identification.

    Uses the Lead's immutable import date (lead_added_at -- see FDLead)
    + the client's own phone number, following the exact same phone +
    nearest-date architecture already used for Missed Call identification
    (routes/flintdesk/tasks.py's _closest_call/_match_call_for_task), rather
    than a second, unrelated system. Phone matching reuses
    services.client_lifecycle.normalize_phone_number (country-code aware,
    7-digit minimum) rather than this module's own narrower _phone() helper,
    since that is the normalizer already used to resolve a client's actual
    phone number elsewhere in FlintDesk.

    This is informational only -- it never sets fd_a_leads_call_id, which
    remains exclusively the explicit Analyse action's job (see
    _analyse_lead_row). It only supplies a proposed Call ID to display
    (_call_details) for a lead that hasn't been analysed yet, the same way
    the Missed Calls page shows an identified Call ID before any explicit
    action has linked it.
    """
    phones_to_match: dict[str, list[FDLead]] = {}
    for row in rows:
        if row.fd_a_leads_call_id:
            continue  # Already has an explicit, authoritative link.
        phone = normalize_phone_number((row.fd_m_leads or {}).get("phone"))
        if phone:
            phones_to_match.setdefault(phone, []).append(row)
    if not phones_to_match:
        return {}

    calls_by_phone: dict[str, list[Call]] = {}
    # Scoped to just these leads' own phone numbers in SQL -- fetching the
    # entire calls table here (as this used to, every column joined to
    # clients) was confirmed in production as the largest single source of
    # database load (33% of total DB time from this exact query shape).
    phone_match_clause = or_(*(
        or_(Call.src.like(f"%{phone}"), Call.dst.like(f"%{phone}"))
        for phone in phones_to_match
    ))
    # Eager-load .client: the identified call returned here ends up in
    # _call_details(), which reads call.client.client_name -- without this,
    # that's a lazy-loaded query per lead instead of one join.
    for call in db.query(Call).options(joinedload(Call.client), load_only(*LEAD_CALL_COLUMNS, raiseload=True)).filter(phone_match_clause).all():
        for digits in {normalize_phone_number(call.src), normalize_phone_number(call.dst)}:
            if digits and digits in phones_to_match:
                calls_by_phone.setdefault(digits, []).append(call)

    identified: dict[UUID, Call] = {}
    for phone, leads_for_phone in phones_to_match.items():
        candidates = calls_by_phone.get(phone)
        if not candidates:
            continue
        for row in leads_for_phone:
            match = _closest_call_by_date(candidates, row.lead_added_at.astimezone(CENTRAL_TIMEZONE).date() if row.lead_added_at else None)
            if match:
                identified[row.id] = match
    return identified


def _outbound_calls_by_lead(db: Session, rows: list[FDLead]) -> dict[UUID, Call]:
    """The outbound call (an agent calling the lead back) that answers each
    lead's phone number, for the Dashboard's Website & Paid Ads table.

    That table tracks what happened on the calls agents actually placed TO
    leads, so only an outbound call counts -- one whose dst is the lead's
    phone and whose src is a configured system/trunk number, the same
    "configured source -> remote destination" routing definition
    _eligible_outbound_calls already uses. Matched by phone + the lead's
    import date (nearest-date, same as _identified_calls_by_lead), batched
    the same way (one query for every call, not one per lead) rather than
    calling _eligible_outbound_calls per lead and re-querying every time.
    """
    phones_to_match: dict[str, list[FDLead]] = {}
    for row in rows:
        phone = normalize_phone_number((row.fd_m_leads or {}).get("phone"))
        if phone:
            phones_to_match.setdefault(phone, []).append(row)
    if not phones_to_match:
        return {}

    system_numbers = _configured_call_numbers(db)
    # Scoped to just these leads' own phone numbers in SQL -- fetching the
    # entire calls table here (as this used to) held a DB connection for
    # the whole scan on every single Leads page load, which was exhausting
    # the connection pool for the whole app.
    dst_match_clause = or_(*(Call.dst.like(f"%{phone}") for phone in phones_to_match))
    calls_by_phone: dict[str, list[Call]] = {}
    for call in db.query(Call).options(load_only(*LEAD_CALL_COLUMNS, raiseload=True)).filter(dst_match_clause).all():
        dst = normalize_phone_number(call.dst)
        if dst in phones_to_match and _phone(call.src) in system_numbers:
            calls_by_phone.setdefault(dst, []).append(call)

    matched: dict[UUID, Call] = {}
    for phone, leads_for_phone in phones_to_match.items():
        candidates = calls_by_phone.get(phone)
        if not candidates:
            continue
        for row in leads_for_phone:
            match = _closest_call_by_date(candidates, row.lead_added_at.astimezone(CENTRAL_TIMEZONE).date() if row.lead_added_at else None)
            if match:
                matched[row.id] = match
    return matched


def _assigned_outbound_calls_by_lead(db: Session, rows: list[FDLead]) -> tuple[dict[UUID, Call], dict[UUID, Call]]:
    """For each ASSIGNED lead (owner saved + import date set): ``(same_day, first_on_or_after)``.

    Both are outbound calls - src is a configured system number, dst is the lead's phone - and dates are Central:

    * ``same_day``: the call made on the lead's Central import date (latest of that day). The Dashboard's "called the same
      day" figure reads this one, so its meaning is unchanged.
    * ``first_on_or_after``: the EARLIEST outbound call made on or after that date, whichever day. The S1 row turns green
      from this one (a call made the next day still counts as "the call was made"), and S1/S2 show "Call made on <date>"
      under the phone when it was not the import date. Calls from before the lead existed never count.
    """
    system_numbers = _configured_call_numbers(db)
    # Only assigned leads (owner + lead_added_at set) are ever looked up
    # below, so only their phone numbers are worth fetching calls for.
    phones_to_match = {
        phone
        for row in rows
        if row.fd_m_leads_owner_id and row.lead_added_at
        and (phone := normalize_phone_number((row.fd_m_leads or {}).get("phone")))
    }
    calls_by_phone: dict[str, list[Call]] = {}
    if phones_to_match:
        # Scoped to just these leads' own phone numbers in SQL -- fetching
        # the entire calls table here (as this used to) held a DB
        # connection for the whole scan on every single Leads page load,
        # which was exhausting the connection pool for the whole app.
        dst_match_clause = or_(*(Call.dst.like(f"%{phone}") for phone in phones_to_match))
        for call in db.query(Call).options(load_only(*LEAD_CALL_COLUMNS, raiseload=True)).filter(dst_match_clause).all():
            phone = normalize_phone_number(call.dst)
            if phone and _phone(call.src) in system_numbers:
                calls_by_phone.setdefault(phone, []).append(call)
    same_day: dict[UUID, Call] = {}
    first_after: dict[UUID, Call] = {}
    for row in rows:
        payload = row.fd_m_leads or {}
        # Owner id is persisted only by the Submit endpoint. Older code wrote
        # submitted_at into the payload, but that field is not a stored
        # FDLead column, so checking it made every reloaded S1 row ineligible
        # for status coloring. The persisted owner relationship is the
        # canonical proof that the assignment was submitted.
        if not row.fd_m_leads_owner_id or not row.lead_added_at:
            continue
        assigned_date = row.lead_added_at.astimezone(CENTRAL_TIMEZONE).date()
        phone = normalize_phone_number(payload.get("phone"))
        candidates = [call for call in calls_by_phone.get(phone, []) if call.call_date and call.call_date >= assigned_date]
        if not candidates:
            continue
        first_after[row.id] = min(candidates, key=lambda call: (call.call_date, _time_to_seconds(call.call_time)))
        same = [call for call in candidates if call.call_date == assigned_date]
        if same:
            same_day[row.id] = max(same, key=lambda call: _time_to_seconds(call.call_time))
    return same_day, first_after


def _same_day_outbound_calls_by_lead(db: Session, rows: list[FDLead]) -> dict[UUID, Call]:
    """Return an assigned lead's outbound call made on its Central import date."""
    return _assigned_outbound_calls_by_lead(db, rows)[0]


def _scoped_calls(db: Session, allowed: set[str] | None, start: date | None = None, end: date | None = None, phone: str | None = None):
    query = db.query(Call).options(joinedload(Call.client))
    if allowed is not None:
        if not allowed:
            return []
        query = query.filter(Call.extension.in_(allowed))
    if start:
        query = query.filter(Call.call_date >= start)
    if end:
        query = query.filter(Call.call_date <= end)
    if phone:
        # Every current caller passes a phone number and only wants calls
        # matching it, but used to fetch and discard the entire calls table
        # (every column, joined to clients) to find those few rows in
        # Python -- confirmed in production as the single largest source of
        # database load (a bulk Analyse over N leads ran this N times,
        # unfiltered, each up to ~49s). Scoping it in SQL is the same fix
        # already applied to _outbound_calls_by_lead/_same_day_outbound_calls_by_lead.
        query = query.filter(or_(Call.src.like(f"%{phone}"), Call.dst.like(f"%{phone}")))
    return query.order_by(Call.call_date.desc(), Call.call_time.desc(), Call.call_id.desc()).all()


def _call_details(call: Call | None, identified: bool = False):
    if not call:
        return {"client_id": "", "date_received": "", "source": "", "name": "", "open_duration": "", "transcript": "", "audio": False, "call_id": "", "call_reference": "", "extension": "", "identified": False}
    # The client ID popup is the source of truth for a client's name; use it
    # here whenever set so every "name" column matches that popup.
    canonical_name = call.client.client_name if call.client else None
    return {
        "client_id": call.client_id or "", "date_received": call.call_date.isoformat() if call.call_date else "",
        "source": call.src or "", "name": (canonical_name or "").strip() or (call.extname or ""), "open_duration": "",
        "transcript": "", "audio": bool(call.audio_s3_uri or call.audio_s3_key),
        "call_id": str(call.call_id), "call_reference": call.call_reference or "", "extension": call.extension or "",
        # True when this call was proposed by date+phone identification
        # (_identified_calls_by_lead) rather than the explicit Analyse
        # action -- lets the UI show it as a suggestion, not a confirmed
        # match.
        "identified": identified,
    }


def _assigned_agent_name(db: Session, call: Call) -> str:
    """Resolve the agent to show in the Leads Stage 2 Agent Name column.

    Reuses the Calls page's own agent resolution (a business-date-aware
    "assignment in effect on this call's date" lookup, overridden by a
    self-identified name when the agent typed one) instead of the separate,
    cruder lookup this used to do, which required assignment_date to equal
    call_date exactly. An assignment stays in effect until replaced, not
    just on the day it was created, so that exact-match almost never held
    -- this column was coming back blank for nearly every lead as a result.
    """
    return _agent_names_with_manual_override(db, [call]).get(str(call.call_id), "-")


def case_status_for_call(call, analysis_by_call_id) -> str:
    """The Case Status Change saved on a call's analysis record ("" when there is no call, no record or none chosen)."""
    if not call:
        return ""
    analysis = analysis_by_call_id.get(str(call.call_id))
    return str(getattr(analysis, "fd_m_case_status_change", "") or "").strip()


def _lead_rows(db: Session, allowed: set[str] | None, owner: FDUser | None = None, *, selected_rows=None):
    query = db.query(FDLead)
    if owner:
        # ``owner_id`` is authoritative. The name clause preserves access to
        # older, correctly named rows until the safe migration/backfill runs.
        query = query.filter(or_(FDLead.fd_m_leads_owner_id == owner.id,
                                 and_(FDLead.fd_m_leads_owner_id.is_(None), FDLead.fd_m_leads_owner == owner.name)))
    rows = selected_rows if selected_rows is not None else query.order_by(*lead_id_order()).all()
    matched_ids = []
    for row in rows:
        if row.fd_a_leads_call_id:
            matched_ids.append(row.fd_a_leads_call_id)
    # Eager-load .client: this feeds _call_details(), which reads
    # call.client.client_name -- without this, that's a lazy-loaded query
    # per lead with a confirmed match instead of one join.
    calls = db.query(Call).options(joinedload(Call.client), load_only(*LEAD_CALL_COLUMNS, raiseload=True)).filter(Call.call_id.in_(matched_ids)).all() if matched_ids else []
    calls_by_id = {call.call_id: call for call in calls}
    # Manual Data Score lives on CallAnalysisResult, keyed by call -- reuse the
    # same Calls-page score/reason here instead of a second scoring system.
    # CallAnalysisResult.call_id is stored as uuid in the database despite the
    # ORM's Text declaration, so comparisons/keys use str() throughout this
    # file's other CallAnalysisResult lookups; match that here too.
    # For leads not yet linked by the explicit Analyse action, propose a Call
    # ID via date+phone identification (see _identified_calls_by_lead) so the
    # Call ID column isn't blank while waiting on that action -- the same
    # role the Missed Calls page's identified Call ID plays there.
    identified_by_lead = _identified_calls_by_lead(db, rows)
    # The S2 "Case Status Change" column shows the status of whichever call the row shows (linked or proposed), read
    # here from that call's analysis record each time, so a later change on the Calls page is what the Leads page shows.
    analysis_call_ids = {str(call_id) for call_id in matched_ids} | {str(call.call_id) for call in identified_by_lead.values()}
    analyses = (
        db.query(CallAnalysisResult)
        .options(load_only(CallAnalysisResult.call_id, CallAnalysisResult.id, CallAnalysisResult.fd_m_case_status_change,
                           CallAnalysisResult.manual_data_score, CallAnalysisResult.manual_data_score_reason))
        .filter(CallAnalysisResult.call_id.in_(sorted(analysis_call_ids)))
        .all()
        if analysis_call_ids else []
    )
    analysis_by_call_id = {str(analysis.call_id): analysis for analysis in analyses}

    # Dashboard "Website & Paid Ads" table: the Case Status Change of the
    # outbound call agents actually placed to this lead (see
    # _outbound_calls_by_lead) -- independent of whether Analyse has ever
    # linked this lead to a call, since that link isn't restricted to
    # outbound calls and this table specifically tracks outbound outcomes.
    outbound_calls_by_lead = _outbound_calls_by_lead(db, rows)
    same_day_outbound_by_lead, first_outbound_by_lead = _assigned_outbound_calls_by_lead(db, rows)
    outbound_call_ids = {call.call_id for call in outbound_calls_by_lead.values()}
    outbound_analysis_by_call_id = (
        {
            str(a.call_id): a
            for a in db.query(CallAnalysisResult).options(load_only(CallAnalysisResult.call_id, CallAnalysisResult.fd_m_case_status_change)).filter(CallAnalysisResult.call_id.in_([str(cid) for cid in outbound_call_ids])).all()
        }
        if outbound_call_ids
        else {}
    )

    result = []
    for row in rows:
        payload = dict(row.fd_m_leads or {})
        if allowed is not None and str(payload.get("extension", "")).strip().lower() not in allowed:
            continue
        call = calls_by_id.get(row.fd_a_leads_call_id)
        is_identified = False
        if not call:
            call = identified_by_lead.get(row.id)
            is_identified = call is not None
        analysis = analysis_by_call_id.get(str(row.fd_a_leads_call_id)) if row.fd_a_leads_call_id else None
        outbound_call = outbound_calls_by_lead.get(row.id)
        if outbound_call:
            outbound_analysis = outbound_analysis_by_call_id.get(str(outbound_call.call_id))
            payload["outbound_call_status"] = (outbound_analysis.fd_m_case_status_change if outbound_analysis else None) or ""
        result.append({
            "id": row.id, "lead_added_at": row.lead_added_at, "record": payload, "call_details": _call_details(call, is_identified),
            # Kept separate from the Analyse/identification call details:
            # this is the narrowly qualified assignment-day outbound call.
            "same_day_outbound_call_id": str(same_day_outbound_by_lead[row.id].call_id) if row.id in same_day_outbound_by_lead else "",
            # The first outbound call made on/after the import date (Central), any day: the S1 row is green when one exists,
            # and "Call made on <date>" is shown under the phone when that date is not the import date.
            "outbound_call_id": str(first_outbound_by_lead[row.id].call_id) if row.id in first_outbound_by_lead else "",
            "outbound_call_date": first_outbound_by_lead[row.id].call_date.isoformat() if row.id in first_outbound_by_lead else "",
            "outbound_call_same_day": row.id in same_day_outbound_by_lead,
            "has_submitted_assignment": bool(row.fd_m_leads_owner_id),
            "lead_score": None, "bos_score": None, "priority_tier": None, "urgency_override": None,
            "case_status_change": case_status_for_call(call, analysis_by_call_id),
            "analysis_id": analysis.id if analysis else None,
            "manual_data_score": analysis.manual_data_score if analysis else None,
            "manual_data_score_reason": analysis.manual_data_score_reason if analysis else None,
        })
    return result


def _analyse_lead_row(row: FDLead, allowed: set[str] | None, db: Session):
    """Populate one lead from the newest matching inbound or outbound call."""
    data = dict(row.fd_m_leads or {})
    missing = [label for key, label in (("lead_id", "Lead ID"), ("phone", "Phone")) if not str(data.get(key) or "").strip()]
    if missing:
        raise HTTPException(status_code=422, detail=f"Analyse requires {' and '.join(missing)}.")
    if allowed is not None and not allowed:
        raise HTTPException(status_code=422, detail="Analyse cannot proceed because no extension is assigned to the logged-in agent.")
    candidates = _eligible_calls(db, allowed, data.get("phone"), row)
    if not candidates:
        raise HTTPException(status_code=404, detail="No matching inbound or outbound call was found for this phone number under your assigned extension.")
    # _scoped_calls is newest-first; retaining that ordering is the established
    # call-selection rule when the same permitted number appears more than once.
    # Try each candidate in that order until one has a finished AI analysis --
    # the newest match is preferred, but it must not block a match against an
    # older candidate that's already analysed while the newest one is still
    # processing.
    call = None
    analysis = None
    for candidate in candidates:
        candidate_analysis = db.query(CallAnalysisResult).filter(CallAnalysisResult.call_id == str(candidate.call_id)).first()
        if candidate_analysis:
            call, analysis = candidate, candidate_analysis
            break
    if not analysis:
        raise HTTPException(status_code=409, detail="AI analysis is not available for the matching call yet.")
    data.update({
        "matched_call_id": str(call.call_id), "extension": call.extension or data.get("extension", ""),
        "match_status": "ambiguous" if len(candidates) > 1 else "matched", "match_count": len(candidates),
        "agent_name": _assigned_agent_name(db, call),
        "call_direction": analysis.call_type or "",
        "call_duration": analysis.call_duration or str(call.billsec or ""),
        "case_type": analysis.fd_a_case_type or analysis.case_type or "",
        "case_worker_assigned": analysis.fd_a_case_worker_assigned or analysis.caseworker_assigned or "",
        "urgency_flag": analysis.fd_a_urgency_flag or "",
        "redirect_escalation_to": analysis.escalation_to or "",
        "call_outcome": analysis.fd_a_call_outcome or "",
        "call_summary": analysis.fd_a_call_summary or analysis.call_summary or "",
        "enquiry_question": analysis.enquiry_details or "",
    })
    row.fd_m_leads = data
    return {"matched": 1, "ambiguous": int(len(candidates) > 1), "call_id": str(call.call_id), "lead_id": data["lead_id"]}


def backfill_unlinked_leads(db: Session, lookback_days: int = 14) -> dict:
    """Retry matching for leads that weren't linked to a call at import time.

    Leads are commonly imported as a same-day call list before the calls
    they describe have actually happened, or before the matching call has
    finished AI analysis -- so the one-shot auto-match _analyse_lead_row
    runs at import time legitimately finds nothing yet and gives up
    (see import_leads). Nothing revisits those leads once the call does
    happen and gets analysed, so they stay unlinked -- and the Leads page
    Stage 2 columns stay blank -- even though the call, with full analysis,
    is sitting right there on the Calls page. This retries the same match,
    unscoped by extension (a background job has no logged-in agent to scope
    to), for every recent lead that's still unlinked.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = (
        db.query(FDLead)
        .filter(FDLead.fd_a_leads_call_id.is_(None), FDLead.lead_added_at >= cutoff)
        .all()
    )
    matched = 0
    for row in rows:
        try:
            _analyse_lead_row(row, None, db)
            db.commit()
            matched += 1
        except HTTPException:
            # No matching or processed call yet -- expected for a lead whose
            # call hasn't happened, or been analysed, yet. Try again next run.
            db.rollback()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("Lead backfill failed for lead %s", row.id)
    return {"checked": len(rows), "matched": matched}


@router.get("/extensions")
def lead_extensions(user=Depends(get_current_user), db: Session = Depends(get_db)):
    allowed = _assigned_extensions(db, user)
    if allowed is None:
        values = db.query(Call.extension).filter(Call.extension.isnot(None)).distinct().all()
        configured = db.query(FDSyncFilter.value).filter(FDSyncFilter.filter_type == "extension").all()
        allowed = {str(value).strip().lower() for (value,) in [*values, *configured] if value}
    # 601, 603 and 605 are routing trunks, not extensions an agent can select.
    return sorted(value for value in allowed if value not in {"601", "603", "605"})


def list_leads_for_dashboard(db: Session, start_date: date, end_date: date):
    """Global lead rows for the server-side Dashboard calculator only.

    Called from services.dashboard_summary, whose output is aggregate KPIs;
    never expose this through a route.
    """
    selected = filtered_leads(db, owner=None, start_date=start_date, end_date=end_date).all()
    return _lead_rows(db, None, None, selected_rows=selected)


@router.get("/by-public-id/{public_lead_id}")
def get_lead_by_public_id(public_lead_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Return every verified persisted field for a business Lead ID."""
    row = db.query(FDLead).filter(FDLead.fd_m_leads_lead_id == public_lead_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found.")
    # Read-only for every signed-in user: the Leads tab lists every lead for everyone.
    details = dict(row.fd_m_leads or {})
    details["lead_id"] = row.fd_m_leads_lead_id
    # Frontend key deliberately stays "created_at" -- FlintDesk/src/components/
    # IdentifierCells.jsx's Lead popup (LEAD_FIELD_LABELS) still reads this
    # exact key; only the backend source attribute was renamed.
    details["created_at"] = row.lead_added_at.isoformat() if row.lead_added_at else None
    # Reuse the persisted analysis fields that drive the Calls table and
    # Dashboard voicemail classification.  If no matching analysis exists,
    # omit them so the UI can honestly show the status as unknown.
    matched_call_id = details.get("matched_call_id") or row.fd_a_leads_call_id
    if matched_call_id:
        analysis = db.query(CallAnalysisResult).filter(
            CallAnalysisResult.call_id == str(matched_call_id)
        ).first()
        if analysis:
            details["voicemail_left_if_no_answer"] = analysis.voicemail_left_if_no_answer
            details["fd_a_call_outcome"] = analysis.fd_a_call_outcome
            details.update(_live_call_fields(analysis))
    return details


def _live_call_fields(analysis: CallAnalysisResult) -> dict:
    """Current Stage 2/3 values for the lead's linked call, as the Calls page shows them.

    The lead's stored snapshot is copied once when Analyse runs, and the agent's
    later entries (action tracking, case status, follow-ups) are saved only on the
    call's analysis row. The popup must show those live values, not the snapshot.
    """
    def text(value):
        if value is None:
            return ""
        return value.isoformat() if hasattr(value, "isoformat") else value

    return {key: text(value) for key, value in {
        "call_direction": analysis.call_type,
        "call_duration": analysis.call_duration,
        "case_type": analysis.fd_a_case_type or analysis.case_type,
        "case_worker_assigned": analysis.fd_a_case_worker_assigned or analysis.caseworker_assigned,
        "urgency_flag": analysis.fd_a_urgency_flag,
        "redirect_escalation_to": analysis.escalation_to,
        "call_outcome": analysis.fd_a_call_outcome,
        "call_summary": analysis.fd_a_call_summary or analysis.call_summary,
        "enquiry_question": analysis.enquiry_details,
        "self_action_item": analysis.fd_m_self_action_item,
        "action_due_date": analysis.fd_m_action_due_date,
        "action_status": analysis.fd_m_manual_action_status,
        "dependency": analysis.fd_m_dependency,
        "follow_up_dates": analysis.fd_m_follow_up_dates,
        "follow_up_attempts": analysis.fd_m_follow_up_attempts,
        "blocker_comment": analysis.fd_m_blocker_comment,
        "case_status": analysis.fd_m_case_status_change,
        "verification_comment": analysis.fd_m_verification_comment,
        "case_closing_date": analysis.case_closing_completed_at,
        "s3_completed": analysis.case_closing_completed,
        "s3_completed_at": analysis.case_closing_completed_at,
    }.items()}



@router.get("")
def list_leads(start_date: date | None = None, end_date: date | None = None, extension: str = "", client_id: str = "", call_id: str = "", lead_number: str = "", lead_id: str = "", client_number: str = "", agent: str = "", dashboard: bool = False, page: int | None = Query(None, ge=1), page_size: int | None = Query(None, ge=1, le=500), user=Depends(get_current_user), db: Session = Depends(get_db)):
    # Every signed-in user sees the same Leads list as a manager does; what an agent may CHANGE is limited in update_lead (Lead Category
    # of their own leads only) and every other mutation stays manager-only. The ``dashboard`` flag is client-supplied and never widens
    # anything; Dashboard KPIs get the global population server-side through list_leads_for_dashboard().
    owner = None
    if start_date is None and end_date is None:
        end_date = datetime.now(CENTRAL_TIMEZONE).date()
        start_date = end_date - timedelta(days=92)
    elif start_date is None:
        start_date = end_date - timedelta(days=92)
    elif end_date is None:
        end_date = start_date + timedelta(days=92)
    if start_date and end_date and (end_date < start_date or (end_date - start_date).days > 92):
        raise HTTPException(400, "Date range must be ordered and 92 days or fewer")
    selected = filtered_leads(
        db, owner=owner, start_date=start_date, end_date=end_date,
        extension=extension, client_id=client_id, call_id=call_id,
        lead_number=lead_number, lead_id=lead_id, client_number=client_number,
        agent=agent, page=page, page_size=page_size,
    ).all()
    return _lead_rows(db, None, owner, selected_rows=selected)


# Manual CSV/paste import is disabled: leads now arrive from the Google Sheet
# (services/lead_sync). Kept commented out for reference.
# @router.post("/import", status_code=201)
# def import_leads(payload: LeadImport, user=Depends(get_current_user), db: Session = Depends(get_db)):
#     _require_manager(user, db)
#     allowed = _assigned_extensions(db, user)
#     if allowed is not None and not allowed:
#         raise HTTPException(status_code=403, detail="No active extension is assigned to this user.")
#     errors, created, updated, analysis_row_ids = [], [], [], []
#     # One server-generated Central-time timestamp represents this single
#     # import operation. It is never accepted from the spreadsheet or UI.
#     imported_at = datetime.now(CENTRAL_TIMEZONE)
#     default_extension = sorted(allowed)[0] if allowed and len(allowed) == 1 else ""
#     for index, source in enumerate(payload.records, 1):
#         record = {key: source.get(key) for key in S1_FIELDS - {"owner"} if source.get(key) not in (None, "")}
#         owner_id = source.get("owner_id")
#         owner = _owner_agent(db, owner_id) if owner_id not in (None, "") else None
#         if "source" in record:
#             record["source"] = _normalise_source(record["source"])
#         received = _lead_received_date(record.get("date_received"))
#         if record.get("date_received") and not received:
#             errors.append({"row": index, "error": "Invalid Date Received. Use MM/DD/YYYY or YYYY-MM-DD."}); continue
#         if received:
#             record["date_received"] = received.isoformat()
#         record["extension"] = str(record.get("extension") or default_extension).strip()
#         if not str(record.get("phone") or "").strip():
#             errors.append({"row": index, "error": "Phone number is required."}); continue
#         if not str(record.get("lead_id") or "").strip():
#             errors.append({"row": index, "error": "Lead ID is required."}); continue
#         invalid_field = next((key for key in FIXED_VALUES if record.get(key) and record[key] not in FIXED_VALUES[key]), None)
#         if invalid_field:
#             errors.append({"row": index, "error": f"Invalid value for {invalid_field}."}); continue
#         if allowed is not None and record["extension"].lower() not in allowed:
#             errors.append({"row": index, "error": "Extension is not assigned to this user."}); continue
#         existing = db.query(FDLead).filter(FDLead.fd_m_leads_lead_id == record["lead_id"].strip()).first()
#         if existing and allowed is not None and str(existing.fd_m_leads_extension or "").strip().lower() not in allowed:
#             errors.append({"row": index, "error": "Lead ID belongs to an extension not assigned to this user."}); continue
#         if owner:
#             # One call, one agent: block assigning this row's phone number
#             # to an agent when it's already owned by a *different* agent on
#             # another lead. Autoflush makes this see earlier rows of this
#             # same import batch too, not just already-committed leads.
#             phone_digits = _phone(record["phone"])
#             conflict = next(
#                 (
#                     other for other in db.query(FDLead).filter(
#                         FDLead.fd_m_leads_owner_id.isnot(None),
#                         FDLead.fd_m_leads_owner_id != owner.id,
#                     ).all()
#                     if _phone(other.fd_m_leads_phone) == phone_digits and (not existing or other.id != existing.id)
#                 ),
#                 None,
#             )
#             if conflict:
#                 errors.append({"row": index, "error": f"Phone number is already assigned to {conflict.fd_m_leads_owner or 'another agent'}."})
#                 continue
#             _set_owner(record, owner)
#         if existing:
#             row = existing
#             # `record` contains only values actually supplied by the importer;
#             # retain all existing data for omitted columns and blank cells.
#             data = dict(row.fd_m_leads or {}); data.update(record); row.fd_m_leads = data
#             updated.append(row)
#             if owner and existing.fd_m_leads_owner_id != owner.id:
#                 _notify_lead_assignment(row, owner, db)
#         else:
#             row = FDLead(id=uuid4(), lead_added_at=imported_at, fd_m_leads=record)
#             db.add(row); created.append(row)
#             if owner:
#                 _notify_lead_assignment(row, owner, db)
#         analysis_row_ids.append(row.id)
#     if errors:
#         db.rollback()
#         raise HTTPException(status_code=422, detail={"message": "Import validation failed; no records were inserted.", "errors": errors})
#     try:
#         # Persist the validated S1 data before attempting optional call
#         # matching.  A missing transcript, an unavailable analysis row, or a
#         # transient matching error must never make the whole import appear to
#         # fail after records have actually been written.
#         db.commit()
#     except SQLAlchemyError as exc:
#         db.rollback()
#         logger.exception("Lead import database commit failed")
#         raise HTTPException(status_code=503, detail="Leads could not be saved. Please retry shortly.") from exc
#
#     # The import request is complete once its validated records are committed.
#     # Auto-analysis is deliberately deferred to the existing background
#     # backfill job: doing an expensive call lookup/commit for every row here
#     # kept the manager's modal spinning after the leads were already visible
#     # to other users, and made the same successful import look unfinished.
#     analysis_deferred = len(analysis_row_ids)
#     return {
#         "inserted": len(created),
#         "updated": len(updated),
#         "analysis_deferred": analysis_deferred,
#     }


@router.post("/analyse")
def analyse_leads(user=Depends(get_current_user), db: Session = Depends(get_db)):
    allowed = _assigned_extensions(db, user)
    if allowed is not None and not allowed:
        raise HTTPException(status_code=422, detail="Analyse cannot proceed because no extension is assigned to the logged-in agent.")
    incomplete = [item["record"].get("lead_id") or item["record"].get("phone") for item in _lead_rows(db, allowed) if not str(item["record"].get("lead_id") or "").strip() or not str(item["record"].get("phone") or "").strip()]
    if incomplete:
        raise HTTPException(status_code=422, detail="Analyse requires both Lead ID and Phone for every S1 lead.")
    matched = ambiguous = unmatched = 0
    failures = []
    for item in _lead_rows(db, allowed):
        row = db.query(FDLead).filter(FDLead.id == item["id"]).first()
        data = dict(row.fd_m_leads or {})
        candidates = _eligible_calls(db, allowed, data.get("phone"), row)
        if not candidates:
            data["match_status"] = "unmatched"; row.fd_m_leads = data; unmatched += 1
            failures.append(str(data.get("lead_id") or "this lead"))
        else:
            call = candidates[0]
            analysis = db.query(CallAnalysisResult).filter(CallAnalysisResult.call_id == str(call.call_id)).first()
            if not analysis:
                failures.append(str(data.get("lead_id") or "this lead"))
                continue
            data.update({
                "matched_call_id": str(call.call_id), "extension": call.extension or data.get("extension", ""),
                "match_status": "ambiguous" if len(candidates) > 1 else "matched", "match_count": len(candidates),
                "agent_name": _assigned_agent_name(db, call),
                "call_direction": analysis.call_type or "",
                "call_duration": analysis.call_duration or str(call.billsec or ""),
                "case_type": analysis.fd_a_case_type or analysis.case_type or "",
                "case_worker_assigned": analysis.fd_a_case_worker_assigned or analysis.caseworker_assigned or "",
                "urgency_flag": analysis.fd_a_urgency_flag or "",
                "redirect_escalation_to": analysis.escalation_to or "",
                "call_outcome": analysis.fd_a_call_outcome or "",
                "call_summary": analysis.fd_a_call_summary or analysis.call_summary or "",
                "enquiry_question": analysis.enquiry_details or "",
            })
            matched += 1; ambiguous += int(len(candidates) > 1)
            row.fd_m_leads = data
    if failures:
        db.rollback()
        raise HTTPException(status_code=422, detail=f"No matching inbound or outbound call was found under your assigned extension for: {', '.join(failures[:10])}.")
    db.commit()
    return {"matched": matched, "ambiguous": ambiguous, "unmatched": unmatched}


@router.post("/{lead_id}/analyse")
def analyse_lead(lead_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    allowed = _assigned_extensions(db, user)
    row = db.query(FDLead).filter(FDLead.id == lead_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found.")
    data = dict(row.fd_m_leads or {})
    if allowed is not None and str(data.get("extension", "")).strip().lower() not in allowed:
        raise HTTPException(status_code=403, detail="You do not have access to this lead.")
    result = _analyse_lead_row(row, allowed, db)
    db.commit()
    return result


@router.delete("/bulk")
def delete_leads(payload: LeadBulkDelete, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _require_manager(user, db)
    allowed = _assigned_extensions(db, user)
    rows = db.query(FDLead).filter(FDLead.id.in_(payload.ids)).all()
    if len(rows) != len(set(payload.ids)):
        raise HTTPException(status_code=404, detail="One or more selected leads were not found.")
    if allowed is not None and any(str(row.fd_m_leads_extension or "").strip().lower() not in allowed for row in rows):
        raise HTTPException(status_code=403, detail="You do not have access to delete one or more selected leads.")
    for row in rows:
        db.delete(row)
    db.commit()
    return {"deleted": len(rows)}


@router.post("/submit")
def submit_leads(payload: LeadBulkDelete, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _require_manager(user, db)
    rows = db.query(FDLead).filter(FDLead.id.in_(payload.ids)).all()
    if len(rows) != len(set(payload.ids)):
        raise HTTPException(status_code=404, detail="One or more selected leads were not found.")
    for row in rows:
        data = dict(row.fd_m_leads or {})
        # Submitting an assignment is not call analysis.  Analysis keeps its
        # own explicit endpoint and may legitimately require a matched call.
        previous_owner_id = row.fd_m_leads_owner_id
        # A Lead is a website/contact workflow, while a Missed Call is a
        # phone-workflow task. The same client (and even the same call link)
        # may legitimately appear in both, so neither assignment may block
        # the other.
        owner = _owner_agent(db, payload.owner_ids.get(row.id) or data.get("owner_id") or data.get("owner"))
        _set_owner(data, owner)
        # An Owner can be selected locally before Submit. That is not an
        # assignment and must not suppress the first Submit notification.
        # Later reassignments always notify the newly selected owner.
        if previous_owner_id != owner.id or not _lead_owner_has_been_notified(row, owner, db):
            _notify_lead_assignment(row, owner, db)
        data["submitted_at"] = datetime.now(timezone.utc).isoformat()
        row.fd_m_leads = data
    db.commit()
    return {"submitted": len(rows)}


@router.patch("/{lead_id}")
def update_lead(lead_id: UUID, payload: LeadPatch, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.field not in ALL_FIELDS:
        raise HTTPException(status_code=400, detail="Unsupported lead field.")
    if payload.field == "lead_id":
        raise HTTPException(status_code=409, detail="Lead ID is immutable; delete and recreate the lead to correct it.")
    if payload.field in FIXED_VALUES and payload.value not in (None, "") and payload.value not in FIXED_VALUES[payload.field]:
        raise HTTPException(status_code=422, detail=f"Invalid value for {payload.field}.")
    row = db.query(FDLead).filter(FDLead.id == lead_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found.")
    data = dict(row.fd_m_leads or {})
    if payload.field == "owner":
        _require_manager(user, db)
        # Assignment is persisted exclusively by /submit.  This endpoint is
        # intentionally not a back door for an onChange mutation.
        raise HTTPException(status_code=409, detail="Choose an Owner locally, then use Submit to assign the lead.")
        previous_owner_id = row.fd_m_leads_owner_id
        owner = _owner_agent(db, payload.value)
        # One call, one agent: this lead's phone number must not already be
        # owned by a *different* agent on another lead -- otherwise two
        # agents could both be calling out to the same person.
        phone_digits = _phone(data.get("phone"))
        if phone_digits:
            conflict = next(
                (
                    other for other in db.query(FDLead).filter(
                        FDLead.id != row.id,
                        FDLead.fd_m_leads_owner_id.isnot(None),
                        FDLead.fd_m_leads_owner_id != owner.id,
                    ).all()
                    if _phone(other.fd_m_leads_phone) == phone_digits
                ),
                None,
            )
            if conflict:
                raise HTTPException(
                    status_code=409,
                    detail=f"This phone number is already assigned to {conflict.fd_m_leads_owner or 'another agent'}.",
                )
        _set_owner(data, owner)
        if previous_owner_id != owner.id:
            _notify_lead_assignment(row, owner, db)
    else:
        is_manager = _can_manage_leads(user, db)
        current_owner = None if is_manager else db.get(FDUser, _current_fd_user_id(user))
        if not is_manager and (not current_owner or (row.fd_m_leads_owner_id != current_owner.id and not (row.fd_m_leads_owner_id is None and row.fd_m_leads_owner == current_owner.name))):
            raise HTTPException(status_code=403, detail="You do not have access to this lead.")
        # An agent may change ONE thing: the Lead Category of a lead they own (checked above). Everything else, Stage 1 and Stage 2 alike,
        # is view-only for agents. Mirrors the rule in the frontend's cell() renderer (Leads.jsx) so this isn't just a UI convenience.
        if not is_manager and payload.field != "lead_category":
            raise HTTPException(status_code=403, detail="Agents may only edit the Lead Category of their own leads.")
        value = payload.value
        if payload.field == "date_received" and value not in (None, ""):
            parsed = _lead_received_date(value)
            if not parsed:
                raise HTTPException(status_code=422, detail="Invalid Date Received. Use MM/DD/YYYY or YYYY-MM-DD.")
            value = parsed.isoformat()
        data[payload.field] = value
    row.fd_m_leads = data
    # Keep fields shared with Calls/Case Closing in their canonical columns too.
    matched_call_id = data.get("matched_call_id")
    canonical = ANALYSIS_FIELD_MAP.get(payload.field)
    if matched_call_id and canonical:
        analysis = db.query(CallAnalysisResult).filter(CallAnalysisResult.call_id == str(matched_call_id)).first()
        if analysis:
            # Reuse the value already normalized above (payload.field ==
            # "owner" never reaches here since it has no canonical mapping).
            if canonical in {"fd_m_action_due_date", "fd_m_date_entry"} and value:
                try:
                    value = date.fromisoformat(str(value)[:10])
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail="Use YYYY-MM-DD for date fields.") from exc
            if canonical == "fd_m_follow_up_attempts" and value not in (None, ""):
                try:
                    value = int(value)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(status_code=422, detail="Follow-up attempts must be a number.") from exc
            setattr(analysis, canonical, value or None)
    db.commit()
    return {"id": row.id, "record": data}
