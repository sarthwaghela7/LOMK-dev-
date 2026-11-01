import logging
import uuid
import os
import re
import shutil
import subprocess
import tempfile
import time
import json
import requests
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, text
from sqlalchemy.orm import Session, defer, joinedload
from sqlalchemy.exc import SQLAlchemyError
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
from imageio_ffmpeg import get_ffmpeg_exe

from storage.db import get_db
from services.follow_up_dates import parse_follow_up_date_param
from storage.models import Call, CallAnalysisResult, Client, FDLead, FDSyncFilter, FDAssignment, FDTask, FDUser
from middleware.auth import get_current_user
from services.call_access import assert_completion_access, assert_manual_call_access
from services.stage_completion import STAGE2_REQUIRED_FIELDS, case_status_change_date, missing_stage2_fields
from services.dashboard_call_scope import is_dashboard_call
from services.hannah_ai_routing import routing_leg_ids_to_exclude as hannah_ai_routing_leg_ids_to_exclude
from services.vitel_metadata import failed_ring_route_leg_ids, inbound_agent_extension
from storage.s3 import S3Service
from utils.call_id_formatter import format_call_id
from utils.config import LLM_API_KEY, LLM_MODEL, LLM_URL
from utils.ttl_cache import cache_get, cache_set, coalesced_read

router = APIRouter(prefix="/calls", tags=["Calls"])


def _fd_assignment_numbers(db: Session, user: dict | None):
    # Call Records visibility is intentionally the same for every
    # authenticated role: an agent's extension assignment governs what they
    # are permitted to edit/act on (see services/call_access.py), not what
    # call data they can view/filter. Returning None here means "no
    # number-based scope" for every caller, so Admin, Team Lead, SME, and
    # Agent all see the identical call population for the same filters.
    return None


def _call_matches_assignment(call: Call, assigned_numbers: set[str] | None, extensions_by_parent: dict[str, set[str]]) -> bool:
    if assigned_numbers is None:
        return True
    call_values = {
        _normalize_routing_number(value)
        for value in (call.src, call.dst, call.extension)
        if value
    }
    if call_values & assigned_numbers:
        return True

    # Resolve every extension-to-lead relationship from Settings instead of
    # retaining 601/603 (or any agent mapping) in application code.
    return any(_call_matches_lead_number(call, assigned_number, extensions_by_parent) for assigned_number in assigned_numbers)


def _assigned_call_ids(db: Session, user: dict | None, start: date | None, end: date | None):
    assigned_numbers = _fd_assignment_numbers(db, user)
    if assigned_numbers is None:
        return None
    extensions_by_parent = _extensions_by_parent_number(db)
    query = db.query(Call)
    if start:
        query = query.filter(Call.call_date >= start)
    if end:
        query = query.filter(Call.call_date <= end)
    return {
        str(call.call_id)
        for call in query.all()
        if _call_matches_assignment(call, assigned_numbers, extensions_by_parent)
    }


def _record_case_status_change(db: Session, record: CallAnalysisResult, previous: str | None, current: str):
    if (previous or "").strip() == current.strip():
        return
    db.flush()
    db.execute(text("""
        INSERT INTO fd_case_status_history (analysis_id, call_id, previous_status, new_status)
        VALUES (:analysis_id, :call_id, :previous_status, :new_status)
    """), {
        "analysis_id": record.id,
        "call_id": record.call_id,
        "previous_status": previous,
        "new_status": current,
    })

# ==================================================
# CHANGELOG (Business page support)
# ==================================================
# 1. Added constants PAID_CONSULTATION_STATUS / BUSINESS_OPPORTUNITY_YES.
# 2. _fetch_filtered_case_rows(): added a `business_opportunity` filter
#    param (used by the new endpoints only; every existing caller passes
#    None so /pending and /success are unaffected).
# 3. _fetch_filtered_case_rows(): the matching_calls query now also
#    selects Call.transcript (needed for the Business table's
#    "transcript" column). This adds one extra selected column to the
#    same existing query used by /pending and /success too — no new
#    queries, no N+1.
# 4. Added BUSINESS_SORT_FIELDS + two new endpoints:
#    GET /calls/business/summary  (KPI cards)
#    GET /calls/business          (paginated table)
# Nothing else in this file was modified.
# ==================================================


# ==================================================
# Pending Cases: allowed action_status dropdown values
# ==================================================
VALID_ACTION_STATUSES = {
    "Open",
    "Success - PC",
    "Success - GE",
    "PC Follow Up",
    "PC shared and lost",
    "Fee details shared & FU",
    "Client Side delay",
    "Moved to next date",
    "Blocker",
    "Dropped",
}


class FollowUpStatusUpdateRequest(BaseModel):
    follow_up_status: str


class VoicemailLeftStatusUpdateRequest(BaseModel):
    voicemail_left_if_no_answer: str


from services.case_lifecycle import CASE_STATUS_OPTIONS, STAGES as LIFECYCLE_STAGES, accepted_status, lifecycle_stage_of
VALID_FD_CASE_STATUS_CHANGE = set(CASE_STATUS_OPTIONS)
# Stage 2 "Action Status" dropdown values (same list the Calls page offers).
# Named separately from VALID_ACTION_STATUSES above, which is the older Pending
# Cases action_status list and must keep its own values.
STAGE2_ACTION_STATUSES = {"Open", "In Progress", "Completed", "Pending", "Not Required"}


# "Not set" token for the Calls page Case Status filter: calls whose Case Status Change is blank (or that have no
# analysis record yet). Every other accepted value is one of the Case Status Change dropdown options.
CASE_STATUS_FILTER_NONE = "__none__"


def _validate_case_status_filter(case_status):
    """Calls-page Case Status filter: blank/None = no filter; otherwise the "not set" token or a real Case Status option.

    The authoritative list is the one the Case Status Change field itself validates against, never a second copy.
    """
    case_status = (case_status or "").strip() or None
    if case_status is None or case_status == CASE_STATUS_FILTER_NONE:
        return case_status
    from services.case_lifecycle import canonical_status
    canonical = canonical_status(case_status)        # also maps a legacy spelling from a stale tab to its new wording
    if canonical is None or canonical.endswith(" & Lost"):
        raise HTTPException(status_code=422, detail="Invalid case_status filter.")
    return canonical


def _validate_aging_dependency_filter(value):
    """Dashboard Open Call Aging popup: whose dependency the open call is waiting on (the saved AI answer); blank = no filter."""
    value = (value or "").strip().lower() or None
    if value is not None and value not in ("agent", "other_agent"):
        raise HTTPException(status_code=422, detail="Invalid aging_dependency filter.")
    return value


def _validate_priority_form_filter(value):
    """Calls-page Priority Cases filter: RFE, I-130, I-485, H4 or H4-EAD (stored as H4EAD); blank = no filter."""
    from services.priority_cases import FORMS
    key = (value or "").strip().upper().replace("-", "").replace(" ", "")
    if not key:
        return None
    by_key = {form.upper().replace("-", ""): form for form in FORMS}
    if key not in by_key:
        raise HTTPException(status_code=422, detail="Invalid priority_form filter.")
    return by_key[key]


def _require_priority_access(priority_form, user):
    """The Priority Cases filter keeps the access of the page it replaces: Admin, Team Lead and Subject Matter Expert."""
    from middleware.auth import has_priority_cases_access
    if priority_form and not has_priority_cases_access(user):
        raise HTTPException(status_code=403, detail="Priority Cases access required")


def _validate_lifecycle_stage_filter(stage):
    """Calls-page lifecycle button: blank = no filter; otherwise one of the five stages (single choice)."""
    stage = (stage or "").strip().lower() or None
    if stage is not None and stage not in LIFECYCLE_STAGES:
        raise HTTPException(status_code=422, detail="Invalid lifecycle_stage filter.")
    return stage


def _validate_action_status_filter(action_status):
    """Only an existing Stage 2 Action Status value may filter."""
    action_status = (action_status or "").strip() or None
    if action_status and action_status not in STAGE2_ACTION_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid action_status filter.")
    return action_status



class ManualFieldsUpdateRequest(BaseModel):
    fd_m_self_action_item: Optional[str] = None
    fd_m_action_due_date: Optional[date] = None
    fd_m_manual_action_status: Optional[str] = None
    fd_m_dependency: Optional[str] = None
    fd_m_follow_up_dates: Optional[str] = None
    fd_m_follow_up_attempts: Optional[int] = None
    fd_m_blocker_comment: Optional[str] = None
    fd_m_case_status_change: Optional[str] = None
    fd_m_date_entry: Optional[date] = None
    fd_m_verification_comment: Optional[str] = None
    fd_m_attachment: Optional[str] = None
    fd_m_google_review: Optional[str] = None
    case_closing_completed: Optional[bool] = None


# ==================================================
# Pending Cases APIs
# ==================================================
# Request model for Pending Cases updates
class PendingCaseUpdateRequest(BaseModel):
    action_status: Optional[str] = None
    action_date: Optional[date] = None
    remarks: Optional[str] = None


# ==================================================
# Success Cases: request models
# ==================================================
# A call only ever has ONE action_status value (it's a single Text column,
# same one Pending Cases uses), so "Success - PC" and "Success - GE" can't
# both be true on one row at once. Success Cases therefore shows any call
# whose action_status is EITHER of those two values (see VALID_ACTION_STATUSES
# above, where both already exist as dropdown options). If your workflow
# needs both to be recorded independently, they'd need to become two
# separate columns; that's outside a "reuse existing columns" change so it
# is not done here.
SUCCESS_ACTION_STATUSES = {"Success - PC", "Success - GE"}

VALID_REVIEW_STATUSES = {"Review Received", "Review Not Received"}
DEFAULT_REVIEW_STATUS = "Review Not Received"

# ==================================================
# Business page: constants  <-- NEW
# ==================================================
# "Converted to Paid Consultation" (KPI Card 2) counts Business
# Opportunity calls whose action_status is "Success - PC" — this is the
# same action_status value already used elsewhere (see
# VALID_ACTION_STATUSES / SUCCESS_ACTION_STATUSES above), not a new status.
PAID_CONSULTATION_STATUS = "Success - PC"
BUSINESS_OPPORTUNITY_YES = "YES"


class ReviewStatusUpdateRequest(BaseModel):
    review_status: str


class ReviewCommentUpdateRequest(BaseModel):
    review_status_comment: Optional[str] = None


class ReviewDateUpdateRequest(BaseModel):
    review_status_updated_date: date


def _normalize_routing_number(value):
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


# Short-TTL cache for small, slow-changing reference data (Settings'
# fd_sync_filters rows, the fd_users name roster). Calls, Leads, Tasks, and
# Dashboard each independently re-query these same few tables on every
# single page load -- often more than once per request already (e.g.
# get_analysis calling _system_numbers, then serialize_analysis_rows
# calling it again). A 60s TTL keeps a Settings change visible well within
# a minute while cutting out redundant round trips this data doesn't need
# fresh on every request. Process-local (not shared across multiple
# workers/dynos) -- fine here since this app runs as a single process.
_REFERENCE_CACHE_TTL_SECONDS = 60
_reference_cache: dict[str, tuple[float, object]] = {}


def _cached_reference(key: str, compute):
    cached = _reference_cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _REFERENCE_CACHE_TTL_SECONDS:
        return cached[1]
    value = compute()
    _reference_cache[key] = (now, value)
    return value


def _extensions_by_parent_number(db: Session) -> dict[str, set[str]]:
    """One-time fetch of Settings' extension -> parent-number configuration.

    Every caller that checks many calls against a lead number needs this same
    small, rarely-changing table. Building it once per request and passing it
    down (instead of the previous per-call query in
    ``_configured_extensions_for_lead``) turns an O(calls) or O(calls x
    assigned numbers) query storm into a single query -- this was the actual
    cause of the Agent role's slow Calls page load, since agent-scoping
    (``_call_matches_assignment``) runs this check for every call on every
    request for that role.
    """
    def _compute():
        mapping: dict[str, set[str]] = {}
        for row in db.query(FDSyncFilter).filter(FDSyncFilter.filter_type == "extension").all():
            parent = _normalize_routing_number(row.parent_number)
            if not parent:
                continue
            mapping.setdefault(parent, set()).add(_normalize_routing_number(row.value))
        return mapping
    return _cached_reference("extensions_by_parent_number", _compute)


def with_lead_ring_route(extensions_by_parent: dict[str, set[str]], lead_number: str | None) -> dict[str, set[str]]:
    """The mapping plus the lead number's own ring route (e.g. 603 for 2243374447), for FILTERING only.

    Calls to a lead line are logged with dst = its ring route (601/603/605) when an agent extension answers
    them, so a Lead number filter must include that route or it undercounts every agent. Never pass the result
    to the assignment/attribution code: who gets credit for a call is decided separately.
    """
    from services.hannah_ai_routing import LEAD_RING_ROUTES

    expected = _normalize_routing_number(lead_number)
    route = LEAD_RING_ROUTES.get(expected)
    if not route:
        return extensions_by_parent
    merged = dict(extensions_by_parent)
    merged[expected] = set(merged.get(expected, set())) | {route}
    return merged


def _call_matches_lead_number(call: Call | None, lead_number: str | None, extensions_by_parent: dict[str, set[str]]) -> bool:
    """Match a lead number against the actual CDR source or destination.

    A lead number can appear directly in ``src``/``dst`` or via its configured
    inbound routing destination (for example, the 603 trunk for 2243374447).
    Extensions are deliberately not part of this predicate. The normalizer
    makes 10/11-digit NANP representations compare consistently without
    changing stored CDR values.
    """
    if not lead_number:
        return True
    expected = _normalize_routing_number(lead_number)
    if not call:
        return False
    src = _normalize_routing_number(call.src)
    dst = _normalize_routing_number(call.dst)
    return (
        src == expected
        or dst == expected
        or dst in extensions_by_parent.get(expected, set())
    )


def _call_direction(call: Call | None, system_numbers: set[str]) -> str | None:
    """Return the same direction projection exposed by call serialization."""
    if not call:
        return None
    if _normalize_routing_number(call.src) in system_numbers:
        return "Outbound"
    if _normalize_routing_number(call.dst) in system_numbers:
        return "Inbound"
    return None


def _call_matches_agent(call: Call | None, requested_agent: str | None, agent_names: dict[str, str]) -> bool:
    if not requested_agent:
        return True
    if not call:
        return False
    return agent_names.get(str(call.call_id), "-").strip().casefold() == requested_agent.strip().casefold()


def _assignment_matches_call(
    call: Call | None,
    assignment_number: str | None,
    assignment_extension: str | None,
    extensions_by_parent: dict[str, set[str]],
) -> bool:
    """Return whether a call belongs to one configured lead-number assignment."""
    if not call:
        return False
    return (
        _effective_extension(call) == str(assignment_extension or "").strip()
        and _call_matches_lead_number(call, assignment_number, extensions_by_parent)
    )


def _effective_analysis_date(row: CallAnalysisResult):
    """Use the indexed normalized date when present, with the safe legacy fallback."""
    return row.call_recording_date_normalized or parse_call_recording_date(row.call_recording_date)


def _is_front_desk_agent(user: FDUser) -> bool:
    # `agent` is the canonical Front Desk Agent role in fd_users.
    return str(user.role or "agent").strip().lower().replace(" ", "_") == "agent"


def _system_numbers(db):
    def _compute():
        configured = db.query(FDSyncFilter).all()
        values = {_normalize_routing_number(row.value) for row in configured}
        values.update(_normalize_routing_number(row.parent_number) for row in configured if row.parent_number)
        return {value for value in values if value}
    return _cached_reference("system_numbers", _compute)


def _missed_call_numbers(db: Session) -> set[str]:
    """Numbers routed to Hannah AI (the automated attendant) -- see
    migration 048. A call to/from one of these belongs only on the
    dedicated Missed Calls page, not the main Calls page. Reuses
    fd_sync_filters' existing target_page column instead of hardcoding
    "602"/"2243860601" here or adding a new column.
    """
    def _compute():
        rows = (
            db.query(FDSyncFilter.value)
            .filter(FDSyncFilter.filter_type == "number", FDSyncFilter.target_page == "missed_calls")
            .all()
        )
        return {_normalize_routing_number(value) for (value,) in rows if value}
    return _cached_reference("missed_call_numbers", _compute)


def _is_missed_call_number(call: Call | None, missed_call_numbers: set[str]) -> bool:
    """dst-only, deliberately -- see services/missed_call_sync.is_missed_call
    for why (dst = 602/2243860601 means the call arrived at Hannah AI;
    checking src too would also match that number appearing as the source
    of some unrelated call)."""
    if not call or not missed_call_numbers:
        return False
    return _normalize_routing_number(call.dst) in missed_call_numbers


def _hannah_ai_routing_leg_ids(calls: list[Call]) -> set:
    """The 601/603 ring-group "not answered" leg of a call session that also
    has a qualifying Hannah AI hand-off (602+2243860601 for Front Desk
    Agent, 604+2243860601 for Website -- see services/hannah_ai_routing) is
    an internal routing leg of a call already represented on the Missed
    Calls page. It must not also surface as its own row on the Calls page.
    Operates entirely on the already-loaded `calls` list -- no extra query.

    Additionally, Vitel's disposition is authoritative for ring-route legs: a
    ring-route leg (601/603/605) that is NO ANSWER / BUSY / FAILED is an
    internal routing attempt and never a Calls-page row, whether or not the
    interaction reached the AI route (602/604 -- that interaction is shown
    once, on Missed Calls). Answered ring-route legs stay normal Calls.
    """
    return hannah_ai_routing_leg_ids_to_exclude(calls) | failed_ring_route_leg_ids(calls)


def _effective_extension(call: Call | None) -> str:
    """The extension to use for the Settings assignment lookup.

    TEMPORARY (Vitel CDR format change, first seen 2026-10-06 13:14 CT): for
    outbound calls Vitel now logs the dialed client number in the CDR
    ``extension`` column instead of the agent extension (101/102/201/203/204/
    205), so no assignment is found and the Agent column shows "-".  The
    recording filename (``out-<dst>-<extension>-<date>-<time>-<uid>.WAV``)
    still carries the real extension, so use it when the call has a linked
    recording and the CDR extension is just a copy of ``dst``.  Calls in the
    original format are unaffected.  Remove once Vitel restores the format.
    """
    extension = str((call.extension if call else "") or "").strip()
    dst = str((call.dst if call else "") or "").strip()
    key = str((getattr(call, "audio_s3_key", "") if call else "") or "")
    if extension and extension == dst and key.lower().endswith(".wav"):
        parts = key.rsplit("/", 1)[-1][:-4].split("-")
        # Agent extension (201) or, as in the original format, the lead number
        # itself filed as the extension (2243374447).
        if len(parts) >= 6 and parts[0].lower() == "out" and re.fullmatch(r"\d{3}|\d{10}", parts[2]):
            return parts[2]
    return extension


def assignment_agent_name(db: Session, call: Call | None) -> str:
    """Resolve the assignment effective on the call's business date.

    Settings assignments remain in effect until replaced.  Requiring an exact
    ``assignment_date == call_date`` match incorrectly leaves every following
    day unassigned.  A call can also predate the oldest assignment record
    Settings has for its extension (the extension was only configured there
    after the call happened); absent any record of a different owner before
    it, that oldest recorded assignment is the best available answer, so it
    is used as a floor instead of leaving the call unassigned.
    """
    if not call or not call.call_date or not _effective_extension(call):
        return "-"
    if _call_direction(call, _system_numbers(db)) == "Inbound" and not inbound_agent_extension(call):
        return "-"  # unanswered/non-agent inbound legs have no extension owner; see assignment_agent_details
    call_date = assignment_business_date(call.call_date)
    extension = _effective_extension(call)
    assignment = db.query(FDAssignment).filter(
        FDAssignment.assignment_date <= call_date,
        FDAssignment.extension == extension,
    ).order_by(FDAssignment.assignment_date.desc(), FDAssignment.created_at.desc()).first()
    if not assignment:
        assignment = db.query(FDAssignment).filter(
            FDAssignment.extension == extension,
        ).order_by(FDAssignment.assignment_date.asc(), FDAssignment.created_at.asc()).first()
    return assignment.agent.name if assignment and assignment.agent else "-"


def assignment_agent_details(db: Session, calls: list[Call]) -> dict[str, tuple[str, str]]:
    """Resolve the lead-number + extension assignment effective on each call date.

    The extension is not globally unique: extension 201 can belong to a
    different agent on different business lines.  Therefore an assignment is
    authoritative only when both its configured lead number and extension
    match the CDR call.  ``_call_matches_lead_number`` normalizes the 10/11
    digit variants and covers configured inbound routing destinations.
    """
    # Inbound agent identification: Vitel now reports the ANSWERING extension
    # on inbound records, so an ANSWERED inbound call resolves through this
    # SAME extension -> agent assignment lookup as outbound (one source of
    # truth, no separate inbound mapping, no manual entry needed).
    #
    # Previously inbound extensions were never an agent (they were the caller's
    # number, then the dialed routing number, e.g. 603; matching that let a
    # leftover trunk-only row hand inbound calls to someone who never touched
    # them). That risk is kept out by inbound_agent_extension(): only an
    # ANSWERED leg whose extension is a plain agent extension (not a
    # ring-route/AI-route/phone number) is attributed; every other inbound leg
    # still has no extension owner. A name typed by an agent on an inbound call
    # still takes precedence (see _agent_names_with_manual_override).
    system_numbers = _system_numbers(db)
    eligible = [
        call for call in calls
        if call and call.call_id and call.call_date and _effective_extension(call)
        and (_call_direction(call, system_numbers) != "Inbound" or inbound_agent_extension(call))
    ]
    if not eligible:
        return {}

    extensions = {_effective_extension(call) for call in eligible}
    assignments = (
        db.query(FDAssignment)
        .options(joinedload(FDAssignment.agent))
        .filter(FDAssignment.extension.in_(extensions))
        .order_by(FDAssignment.extension, FDAssignment.assignment_date.desc(), FDAssignment.created_at.desc())
        .all()
    )
    extensions_by_parent = _extensions_by_parent_number(db)
    history_by_assignment: dict[tuple[str, str], list[FDAssignment]] = {}
    active_by_extension: dict[str, list[FDAssignment]] = {}
    for assignment in assignments:
        number = _normalize_routing_number(assignment.number)
        extension = str(assignment.extension or "").strip()
        if number and extension:
            history_by_assignment.setdefault((number, extension), []).append(assignment)
            if assignment.is_active:
                active_by_extension.setdefault(extension, []).append(assignment)

    def effective_assignment(call: Call):
        call_date = assignment_business_date(call.call_date)
        extension = _effective_extension(call)
        matching_history = [
            assignment
            for (number, candidate_extension), history in history_by_assignment.items()
            if candidate_extension == extension
            and _assignment_matches_call(call, number, candidate_extension, extensions_by_parent)
            for assignment in history
        ]
        matching_history.sort(
            key=lambda assignment: (assignment.assignment_date, assignment.created_at),
            reverse=True,
        )
        for assignment in matching_history:
            if assignment.assignment_date <= call_date:
                return (
                    str(assignment.agent_id),
                    assignment.agent.name if assignment.agent else "-",
                )
        if matching_history:
            # The matching history is ordered newest-first, so the last entry
            # is the oldest configured ownership for this lead + extension.
            oldest = matching_history[-1]
            return (str(oldest.agent_id), oldest.agent.name if oldest.agent else "-")

        # A lead number can be moved while calls from the previous number are
        # still on the same business date. Those stored CDRs must remain
        # visible to the extension's current owner. Use this fallback only
        # when the extension has one unambiguous active owner; a shared
        # extension across multiple active assignments still requires the
        # normal lead-number match above.
        active_assignments = active_by_extension.get(extension, [])
        active_owner_ids = {str(assignment.agent_id) for assignment in active_assignments}
        if len(active_owner_ids) == 1:
            active = active_assignments[0]
            return (str(active.agent_id), active.agent.name if active.agent else "-")
        return ("", "-")

    return {
        str(call.call_id): effective_assignment(call)
        for call in eligible
    }


def assignment_agent_names(db: Session, calls: list[Call]) -> dict[str, str]:
    """Return the display-name projection used by existing call endpoints."""
    return {call_id: details[1] for call_id, details in assignment_agent_details(db, calls).items()}


# Historically saved placeholder values that must never be treated as a real
# self-identified agent name (in display or in filter matching).
_AGENT_PLACEHOLDER_VALUES = {"-", "n/a", "na", "none", "unassigned"}


def _canonical_agent_names(db: Session) -> dict[str, str]:
    """Map every registered FD user's name, casefolded, to its exact spelling."""
    def _compute():
        return {
            str(name).strip().casefold(): str(name).strip()
            for (name,) in db.query(FDUser.name).all()
            if name and str(name).strip()
        }
    return _cached_reference("canonical_agent_names", _compute)


# Below this similarity ratio, two names are treated as genuinely different
# agents rather than a typo of one another. 0.85 comfortably catches a
# single-character slip (e.g. "Gokhul" vs "Gokul" scores ~0.91) while staying
# well above the ratio between any two distinct names on the current roster
# (the closest unrelated pair scores well under 0.8).
_AGENT_NAME_FUZZY_CUTOFF = 0.85


def _resolve_canonical_agent_name(raw_name: str, canonical_by_casefold: dict[str, str]) -> str | None:
    """Resolve a manually typed agent name to its registered spelling.

    front_desk_person_name is free text, so it accumulates real mis-spellings
    (e.g. the long-standing "Gokhul" vs the registered "Gokul") -- not just
    case or whitespace differences. A plain casefolded dict lookup only
    catches the latter, so those mis-spelled rows would keep failing to
    match the correctly spelled name everywhere: the Agent filter, the
    Agent column, and anything else keyed off this. Fall back to a close
    (but not loose) similarity match against the registered roster before
    giving up.
    """
    key = raw_name.strip().casefold()
    if not key:
        return None
    exact = canonical_by_casefold.get(key)
    if exact:
        return exact
    best_match, best_ratio = None, 0.0
    for candidate_key, candidate_name in canonical_by_casefold.items():
        ratio = SequenceMatcher(None, key, candidate_key).ratio()
        if ratio > best_ratio:
            best_match, best_ratio = candidate_name, ratio
    return best_match if best_ratio >= _AGENT_NAME_FUZZY_CUTOFF else None


def _agent_names_with_manual_override(db: Session, calls: list[Call]) -> dict[str, str]:
    """Extension-assignment agent names, overridden by a self-identified name.

    Inbound calls routed through a shared/trunk extension have no reliable
    per-extension owner, so an agent who picks one up types their own name
    into the Agent cell (CallAnalysisResult.front_desk_person_name). That
    self-identification must count as ownership everywhere a call is matched
    to an agent -- not just in the Agent column's display text, which already
    applies this same override in serialize_analysis_rows -- otherwise the
    Agent filter (and anything else keyed off this dict) never finds a call
    the agent explicitly claimed.

    The override is only trusted when it resolves to a real registered
    agent (exact match, or a close typo of one -- see
    _resolve_canonical_agent_name); anything else -- a stray note, an
    unrecognized name -- falls back to the extension-assignment name instead
    of poisoning matching with an unrecognized string. This is a query-time
    correction, so it also fixes every historically mis-spelled
    front_desk_person_name without a data migration.
    """
    agent_names = assignment_agent_names(db, calls)
    call_ids = [str(call.call_id) for call in calls if call and call.call_id]
    if not call_ids:
        return agent_names
    canonical_by_casefold = _canonical_agent_names(db)
    system_numbers = _system_numbers(db)
    calls_by_id = {str(call.call_id): call for call in calls if call and call.call_id}
    manual_rows = (
        db.query(CallAnalysisResult.call_id, CallAnalysisResult.front_desk_person_name)
        .filter(CallAnalysisResult.call_id.in_(call_ids))
        .all()
    )
    for call_id, manual_name in manual_rows:
        manual_name = (manual_name or "").strip()
        if not manual_name or manual_name.casefold() in _AGENT_PLACEHOLDER_VALUES:
            continue
        # A call made through an assigned extension + lead number belongs to
        # that assignment's agent even if someone typed another name. The
        # exception is an inbound call: it is shared work (any agent may
        # claim it), so the name the agent typed decides who handled it even
        # when the answering extension is assigned to someone else.
        # Inbound has no typed-name override: the answering extension decides.
        if _call_direction(calls_by_id.get(str(call_id)), system_numbers) == "Inbound":
            continue
        if agent_names.get(str(call_id), "-") not in ("", "-"):
            continue
        canonical = _resolve_canonical_agent_name(manual_name, canonical_by_casefold)
        if canonical:
            agent_names[str(call_id)] = canonical
    return agent_names


def assignment_business_date(value: date | datetime) -> date:
    """Normalize timestamp inputs to the Settings assignment business day."""
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(ZoneInfo("America/Chicago")).date()
    return value


def _apply_call_routing(data, src, dst, system_numbers):
    src_number = _normalize_routing_number(src)
    dst_number = _normalize_routing_number(dst)
    src_is_system = src_number in system_numbers
    dst_is_system = dst_number in system_numbers
    dst_is_unknown = not dst_number or str(dst).strip().lower() == "unknown"
    src_is_unknown = not src_number or str(src).strip().lower() == "unknown"

    if src_is_system and (dst_is_unknown or not dst_is_system):
        data["call_direction"] = "Outbound"
        data["client_phone_number"] = dst if not dst_is_unknown else None
    elif dst_is_system and (src_is_unknown or not src_is_system):
        data["call_direction"] = "Inbound"
        data["client_phone_number"] = src if not src_is_unknown else None
    elif src_is_system:
        data["call_direction"] = "Outbound"
        data["client_phone_number"] = dst if not dst_is_unknown else None
    elif dst_is_system:
        data["call_direction"] = "Inbound"
        data["client_phone_number"] = src if not src_is_unknown else None

    return data


# Internal ingestion bookkeeping, never part of the public call payload (keeps
# the API contract unchanged by the Vitel recording-filename column).
_INTERNAL_COLUMNS = frozenset({"vitel_recording_filename"})


def serialize_sqlalchemy(obj, db=None, system_numbers=None, exclude_columns=None, agent_name=None):
    """Convert SQLAlchemy model instance to a plain dict."""
    data = {}
    excluded = set(exclude_columns or ()) | _INTERNAL_COLUMNS
    for col in obj.__table__.columns.keys():
        if col in excluded:
            continue
        val = getattr(obj, col)
        if val is None:
            data[col] = None
        elif col == "raw_llm_json" and isinstance(val, str):
            try:
                data[col] = json.loads(val)
            except json.JSONDecodeError:
                data[col] = val
        elif hasattr(val, 'isoformat'):
            data[col] = val.isoformat()
        else:
            data[col] = val
    
    # Add formatted_call_id for Call and CallAnalysisResult objects
    if isinstance(obj, Call):
        data['formatted_call_id'] = format_call_id(obj.call_number)
        if agent_name is not None:
            data['front_desk_person_name'] = agent_name
        elif db is not None:
            data['front_desk_person_name'] = assignment_agent_name(db, obj)
        if system_numbers is None and db is not None:
            system_numbers = _system_numbers(db)
        if system_numbers:
            _apply_call_routing(data, obj.src, obj.dst, system_numbers)
    elif isinstance(obj, CallAnalysisResult):
        data['formatted_call_id'] = format_call_id(obj.call_number)
    
    return data


def parse_call_recording_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value

    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


_MANUAL_CALL_FIELDS = (
    "front_desk_person_name",
    "fd_m_self_action_item",
    "fd_m_verification_comment",
    "fd_m_dependency",
    "fd_m_manual_action_status",
    "fd_m_blocker_comment",
    "fd_m_follow_up_dates",
    "fd_m_case_status_change",
)


def _physical_call_key(call: Call):
    """Stable identity for a Vitel call even when its billsec is revised."""
    return (
        call.call_date,
        call.call_time,
        str(call.src or "").strip(),
        str(call.dst or "").strip(),
    )


def _collapse_physical_calls(calls, analysis_by_call_id=None):
    """Return one operational row per physical call without deleting history.

    Older F1 runs used billsec in their uniqueness key. Vitel can revise that
    value for the same CDR, leaving several rows for one recording. Keep the
    row with user-entered data first, then the one with mapped audio, so the
    Calls page remains usable while raw source rows stay available for audit.
    """
    analysis_by_call_id = analysis_by_call_id or {}
    selected = {}

    def score(call):
        analysis = analysis_by_call_id.get(str(call.call_id))
        has_manual_data = bool(analysis and any(
            str(getattr(analysis, field, "") or "").strip()
            for field in _MANUAL_CALL_FIELDS
        ))
        has_audio = bool(call.audio_s3_uri or call.audio_s3_key)
        is_processed = str(call.processing_status or "").casefold() == "processed"
        # Prefer the earliest durable call reference for otherwise equal CDRs.
        call_number = call.call_number if call.call_number is not None else 2**31
        return (has_manual_data, has_audio, is_processed, int(call.billsec or 0), -call_number)

    for call in calls:
        key = _physical_call_key(call)
        current = selected.get(key)
        if current is None or score(call) > score(current):
            selected[key] = call
    return list(selected.values())


def serialize_analysis_rows(rows, db, include_raw=True, calls_by_id=None, include_transcript=True, summary_rows=False):
    system_numbers = _system_numbers(db)
    call_ids = {str(row.call_id) for row in rows if row.call_id}
    # A caller that already loaded these exact Call rows (get_analysis does,
    # to apply its own filters before this ever runs) can pass that dict in
    # directly instead of this doing the identical query a second time.
    if calls_by_id is None:
        calls = (
            db.query(Call)
            .filter(Call.call_id.in_(call_ids))
            .all()
            if call_ids
            else []
        )
        calls_by_id = {str(call.call_id): call for call in calls if str(call.call_id) in call_ids}
    else:
        calls_by_id = {cid: call for cid, call in calls_by_id.items() if cid in call_ids}
    calls = list(calls_by_id.values())
    leads_by_call = {
        str(lead.fd_a_leads_call_id): lead.fd_m_leads_lead_id
        for lead in db.query(FDLead).filter(FDLead.fd_a_leads_call_id.in_(call_ids)).all()
    } if call_ids else {}
    client_ids = {call.client_id for call in calls if call.client_id}
    client_profiles_by_id = {
        client.client_id: client
        for client in db.query(Client).filter(Client.client_id.in_(client_ids)).all()
    } if client_ids else {}
    agent_names = assignment_agent_names(db, calls)
    canonical_by_casefold = _canonical_agent_names(db)
    system_numbers = _system_numbers(db)
    # Logs that inherit another agent's confirmed closure (one batched query for the page).
    from services.case_closure import inherited_closures
    closure_by_analysis = inherited_closures(db, [getattr(row, "id", None) for row in rows])
    serialized = []
    for row in rows:
        data = serialize_sqlalchemy(
            row,
            # Preserve the deployed paginated response shape.  Full summary
            # text is fetched through the dedicated lightweight endpoint
            # below, rather than changing this hot read path.
            exclude_columns=(set() if include_raw else {"raw_llm_json"}) |
                ({"call_summary", "fd_a_call_summary", "enquiry_details", "evaluator_notes"} if summary_rows else set()),
        )
        call = calls_by_id.get(str(row.call_id))
        data["src"] = call.src if call else None
        data["dst"] = call.dst if call else None
        data["client_id"] = call.client_id if call else None
        data["extension"] = call.extension if call else None
        data["lead_id"] = leads_by_call.get(str(row.call_id))
        # Transcript/audio/duration live only on Call, not on this analysis
        # row -- the Call object is already loaded above for src/dst/
        # extension, so this is free. Included so a paginated Calls page
        # fetch can rely on this one endpoint instead of also fetching the
        # full, unpaginated /calls/ list just for these fields.
        data["transcript"] = call.transcript if call and include_transcript else None
        data["audio_s3_uri"] = call.audio_s3_uri if call else None
        data["audio_s3_key"] = call.audio_s3_key if call else None
        data["billsec"] = call.billsec if call else None
        # Vitel's disposition, so the UI can tell answered/unanswered calls apart.
        data["disposition"] = call.disposition if call else None
        data["call_time"] = call.call_time.isoformat() if call and call.call_time else None
        data["registered_at"] = call.registered_at.isoformat() if call and call.registered_at else None
        # An agent who self-identified on a call they handled (inbound calls
        # have no reliable per-extension owner) takes priority over the
        # extension-assignment-derived name.
        assigned_agent_name = agent_names.get(str(row.call_id), "-")
        manually_set_agent = (row.front_desk_person_name or "").strip()
        # Placeholder values were historically saved by the model and must
        # never override the configured assignment in Calls-page filtering.
        if manually_set_agent.casefold() in _AGENT_PLACEHOLDER_VALUES:
            manually_set_agent = ""
        else:
            # Snap a mis-spelled/mis-cased name (e.g. the long-standing
            # "Gokhul" vs "Gokul" mix-up) to the registered spelling so the
            # Agent column and every name-based filter agree with the
            # extension-based one.
            resolved = _resolve_canonical_agent_name(manually_set_agent, canonical_by_casefold)
            if resolved:
                manually_set_agent = resolved
        data["assigned_agent_name"] = assigned_agent_name
        data["closure_inherited"] = closure_by_analysis.get(getattr(row, "id", None))
        # Persisted stage (migration 073); derived from the status for a record not categorised yet.
        data["case_lifecycle_stage"] = row.case_lifecycle_stage or lifecycle_stage_of(row.fd_m_case_status_change)
        # The fast /calls/{id}/field edit endpoint's stale-write guard
        # compares its expected_value against the raw stored column
        # (getattr(analysis, "front_desk_person_name")), not this computed
        # display value below. Expose the raw value separately so a client
        # editing this field sends the right "value shown when editing
        # began" -- otherwise self-identifying on a call that's merely
        # showing an extension-assignment fallback (nobody has actually
        # typed a name into this column yet) always 409s, since the raw
        # column is still blank while the display already shows a name.
        data["raw_front_desk_person_name"] = row.front_desk_person_name
        # The assigned extension owner wins; a typed name only claims calls
        # with no assigned owner. Inbound has no typed-name fallback: the
        # answering extension decides (matches the Agent filter).
        data["front_desk_person_name"] = (
            assigned_agent_name if assigned_agent_name not in ("", "-")
            else assigned_agent_name if _call_direction(call, system_numbers) == "Inbound"
            else manually_set_agent or assigned_agent_name
        )
        # The client_id record is the source of truth for the display name once set.
        profile = client_profiles_by_id.get(data["client_id"])
        if profile and str(profile.client_name or "").strip():
            # Client profile values are user-managed canonical identity data,
            # so they must override older per-call LLM guesses everywhere.
            data["fd_a_client_name"] = profile.client_name
            data["fd_m_client_name"] = profile.client_name
        if profile and str(profile.client_email or "").strip():
            data["fd_a_email_address"] = profile.client_email
        _apply_call_routing(data, data["src"], data["dst"], system_numbers)
        serialized.append(data)
    return serialized


# Both /calls/ and /calls/analysis filter in SQL by date range, then do
# extensive Python-side processing (assignment scoping, Hannah AI routing
# exclusion, agent-name resolution, serialization) over every row in that
# range, on the same DB session/connection the whole time. A client asking
# for the full history (e.g. a stale cached tab from before this table's
# default range was narrowed) turns into a request that processes the
# entire table and holds its connection for tens of seconds -- confirmed
# in production: repeated 1500-2500+ row responses right alongside
# `QueuePool limit ... reached` timeouts, while a normal single-day
# request returns ~60-80 rows in well under a second. Capping the range
# server-side stops any one request from doing that regardless of what a
# client sends.
MAX_CALLS_DATE_RANGE_DAYS = 92


def _reject_oversized_date_range(start: Optional[date], end: Optional[date]) -> None:
    if start and end and end < start:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")
    if start and end and (end - start).days > MAX_CALLS_DATE_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Date range too large ({(end - start).days} days). "
                f"Please narrow it to {MAX_CALLS_DATE_RANGE_DAYS} days or fewer."
            ),
        )


def _serialize_call_records(db: Session, calls: list) -> list[dict]:
    """The /calls/ row shape (agent name, linked lead id, client profile)."""
    system_numbers = _system_numbers(db)
    agent_names = _agent_names_with_manual_override(db, calls)

    # Serialize to include call_reference and all other fields
    call_ids = [call.call_id for call in calls]
    leads_by_call = {
        str(lead.fd_a_leads_call_id): lead.fd_m_leads_lead_id
        for lead in db.query(FDLead).filter(FDLead.fd_a_leads_call_id.in_(call_ids)).all()
    } if call_ids else {}
    client_ids = {call.client_id for call in calls if call.client_id}
    client_profiles_by_id = {
        client.client_id: client
        for client in db.query(Client).filter(Client.client_id.in_(client_ids)).all()
    } if client_ids else {}
    result = [
        {
            **serialize_sqlalchemy(
            call,
            exclude_columns={"transcript"},
            system_numbers=system_numbers,
            agent_name=agent_names.get(str(call.call_id), "-"),
            ),
            "assigned_agent_name": agent_names.get(str(call.call_id), "-"),
            "lead_id": leads_by_call.get(str(call.call_id)),
            "client_name": str(getattr(client_profiles_by_id.get(call.client_id), "client_name", "") or ""),
            "client_email": str(getattr(client_profiles_by_id.get(call.client_id), "client_email", "") or ""),
        }
        for call in calls
    ]
    
    # Log a sample to verify call_reference is included
    if result:
        print(f"[API] Returning {len(result)} calls with fields: {list(result[0].keys())}")
    
    return result



@router.get("/")
@coalesced_read("calls_list", ttl_seconds=10, exclude={"db"})
def get_calls(
    start_date: date = Query(...),
    end_date: date = Query(...),
    number: Optional[str] = Query(None),
    extension: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    call_direction: Optional[str] = Query(None, pattern="^(?i:inbound|outbound)$"),
    client_id: Optional[str] = Query(None),
    client_number: Optional[str] = Query(None),
    call_id: Optional[str] = Query(None),
    lead_id: Optional[str] = Query(None),
    target_page: str = Query("process_calls"),
    only_unanalyzed: bool = Query(False),
    open_cases: bool = Query(False),
    action_status: Optional[str] = Query(None),
    case_status: Optional[str] = Query(None),
    lifecycle_stage: Optional[str] = Query(None),
    aging_dependency: Optional[str] = Query(None),
    priority_form: Optional[str] = Query(None),
    aging_days: Optional[int] = Query(None, ge=0, le=365),
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=500),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Fetch calls between two dates.
    Returns: call_id, call_reference, call_number, date, time, src, dst,
             extension, transcript, processing_status, audio_s3_uri, and more.
    """
    _reject_oversized_date_range(start_date, end_date)
    action_status = _validate_action_status_filter(action_status)
    case_status = _validate_case_status_filter(case_status)
    lifecycle_stage = _validate_lifecycle_stage_filter(lifecycle_stage)
    aging_dependency = _validate_aging_dependency_filter(aging_dependency)
    priority_form = _validate_priority_form_filter(priority_form)
    _require_priority_access(priority_form, user)
    if agent:
        # Agent filtering and Lead Number/Extension filtering are mutually
        # exclusive in the Calls page UI. If a caller supplies both anyway
        # (a stale client, or a direct API call), agent takes precedence
        # instead of silently ANDing them into a misleadingly narrow result.
        number = None
        extension = None
    from services.analysis_read_query import analysis_read
    calls, _, _ = analysis_read(
        db, start=start_date, end=end_date, call_source=True,
        dashboard=target_page == "dashboard", number=number,
        extension=extension, agent=agent, call_direction=call_direction,
        client_id=client_id, client_number=client_number,
        call_id=call_id, lead_id=lead_id,
        target_page=target_page, page=page, page_size=page_size,
        only_unanalyzed=only_unanalyzed,
        open_cases=open_cases, action_status=action_status, case_status=case_status, lifecycle_stage=lifecycle_stage, aging_dependency=aging_dependency, aging_days=aging_days, priority_form=priority_form,
    )
    return _serialize_call_records(db, calls)


class CallSummaryLookup(BaseModel):
    call_ids: list[UUID]


@router.post("/analysis/summaries")
def get_analysis_summaries(payload: CallSummaryLookup, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return only the summary/question text for the currently visible Calls page."""
    call_ids = list(dict.fromkeys(str(value) for value in payload.call_ids))
    if not call_ids:
        return {}
    if len(call_ids) > 500:
        raise HTTPException(422, "At most 500 calls can be requested at once.")
    rows = db.query(
        CallAnalysisResult.call_id,
        CallAnalysisResult.fd_a_call_summary,
        CallAnalysisResult.call_summary,
        CallAnalysisResult.enquiry_details,
    ).filter(CallAnalysisResult.call_id.in_(call_ids)).all()
    return {
        str(row.call_id): {
            "fd_a_call_summary": row.fd_a_call_summary or row.call_summary or "",
            "enquiry_details": row.enquiry_details or "",
        }
        for row in rows
    }


@router.get("/{call_id}/content")
def get_call_content(call_id: UUID, include_raw: bool = False,
                     db: Session = Depends(get_db), user: dict = Depends(get_current_user), include_analysis: bool = False):
    """Heavy fields are fetched only when an authenticated user opens a call."""
    row = db.query(Call.transcript).filter(Call.call_id == call_id).first()
    if row is None:
        raise HTTPException(404, "Call not found")
    result = {"call_id": str(call_id), "transcript": row.transcript or ""}
    if include_analysis:
        detail = db.query(CallAnalysisResult.call_summary, CallAnalysisResult.fd_a_call_summary,
                          CallAnalysisResult.enquiry_details, CallAnalysisResult.evaluator_notes).filter(
                              CallAnalysisResult.call_id == str(call_id)).first()
        result["analysis"] = dict(detail._mapping) if detail else {}
    if include_raw:
        analysis = db.query(CallAnalysisResult.raw_llm_json).filter(
            CallAnalysisResult.call_id == str(call_id)).first()
        result["raw_llm_json"] = analysis.raw_llm_json if analysis else None
    return result


@router.get("/analysis")
@coalesced_read("calls_analysis", ttl_seconds=10, exclude={"db"})
def get_analysis(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    dashboard: bool = Query(False),
    number: Optional[str] = Query(None),
    extension: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    call_direction: Optional[str] = Query(None, pattern="^(?i:inbound|outbound)$"),
    client_id: Optional[str] = Query(None),
    client_number: Optional[str] = Query(None),
    call_id: Optional[str] = Query(None),
    lead_id: Optional[str] = Query(None),
    call_kind: Optional[str] = Query(None, pattern="^(?i:normal|voicemail)$"),
    review_status: Optional[str] = Query(None, pattern="^(?i:pending|completed)$"),
    include_raw: bool = Query(False),
    open_cases: bool = Query(False),
    action_status: Optional[str] = Query(None),
    case_status: Optional[str] = Query(None),
    lifecycle_stage: Optional[str] = Query(None),
    aging_dependency: Optional[str] = Query(None),
    priority_form: Optional[str] = Query(None),
    aging_days: Optional[int] = Query(None, ge=0, le=365),
    follow_up_date: Optional[str] = Query(None),
    target_page: str = Query("process_calls"),
    cursor: Optional[str] = None,
    cursor_mode: bool = False,
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=500),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Fetch analysis records.
    Returns: call_id, call_reference, call_number, call_recording_date,
             call_duration, LLM fields, quality_score, and more.
    """
    action_status = _validate_action_status_filter(action_status)
    case_status = _validate_case_status_filter(case_status)
    lifecycle_stage = _validate_lifecycle_stage_filter(lifecycle_stage)
    aging_dependency = _validate_aging_dependency_filter(aging_dependency)
    priority_form = _validate_priority_form_filter(priority_form)
    _require_priority_access(priority_form, user)
    follow_up_day = parse_follow_up_date_param(follow_up_date)
    if agent:
        # See the matching guard in get_calls() above: agent and
        # number/extension filtering are mutually exclusive, so agent wins
        # if a caller ever supplies both.
        number = None
        extension = None
    start = parse_call_recording_date(start_date) if start_date else None
    end = parse_call_recording_date(end_date) if end_date else None
    _reject_oversized_date_range(start, end)
    # A missing bound (either or both) used to mean "unfiltered" -- both the
    # SQL query below and _assigned_call_ids() then scanned the entire
    # table. Confirmed in production as a major source of database load, so
    # a missing bound now defaults to the same 92-day window the explicit
    # cap above already enforces, instead of falling through to unbounded.
    if not start and not end:
        end = date.today()
        start = end - timedelta(days=MAX_CALLS_DATE_RANGE_DAYS)
    elif not start:
        start = end - timedelta(days=MAX_CALLS_DATE_RANGE_DAYS)
    elif not end:
        end = start + timedelta(days=MAX_CALLS_DATE_RANGE_DAYS)
    from services.analysis_read_query import analysis_read
    cursor_output = {}
    rows, calls_by_id, total_records = analysis_read(
        db, start=start, end=end, dashboard=dashboard, number=number,
        extension=extension, agent=agent, call_direction=call_direction,
        client_id=client_id, client_number=client_number, call_id=call_id,
        lead_id=lead_id, call_kind=call_kind, review_status=review_status,
        target_page=target_page, page=page, page_size=page_size,
        cursor=cursor, cursor_mode=cursor_mode, cursor_output=cursor_output,
        summary_rows=True, open_cases=open_cases, action_status=action_status, case_status=case_status, lifecycle_stage=lifecycle_stage, aging_dependency=aging_dependency, aging_days=aging_days, priority_form=priority_form,
        follow_up_date=follow_up_day,
    )
    result = serialize_analysis_rows(rows, db, include_raw=False,
                                     calls_by_id=calls_by_id, include_transcript=False, summary_rows=True)
    if page and page_size:
        return {"items": result, "pagination": {
            "page": page, "page_size": page_size, "total_records": total_records,
            "total_pages": max(1, -(-total_records // page_size)),
            **cursor_output,
        }}
    return result


@router.get("/analysis/follow-up-dates")
@coalesced_read("calls_follow_up_dates", ttl_seconds=10, exclude={"db"})
def get_follow_up_dates(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    number: Optional[str] = Query(None),
    extension: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    call_direction: Optional[str] = Query(None, pattern="^(?i:inbound|outbound)$"),
    client_id: Optional[str] = Query(None),
    client_number: Optional[str] = Query(None),
    call_id: Optional[str] = Query(None),
    lead_id: Optional[str] = Query(None),
    call_kind: Optional[str] = Query(None, pattern="^(?i:normal|voicemail)$"),
    review_status: Optional[str] = Query(None, pattern="^(?i:pending|completed)$"),
    open_cases: bool = Query(False),
    action_status: Optional[str] = Query(None),
    case_status: Optional[str] = Query(None),
    lifecycle_stage: Optional[str] = Query(None),
    aging_dependency: Optional[str] = Query(None),
    priority_form: Optional[str] = Query(None),
    aging_days: Optional[int] = Query(None, ge=0, le=365),
    target_page: str = Query("calls"),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Distinct follow-up dates (ISO, ascending) of the calls the Calls table would list.

    Takes the same filters as GET /calls/analysis; the From/To range applies to the call
    date, exactly like the table. Entries that are not valid dates (N/A, legacy text) are skipped.
    """
    action_status = _validate_action_status_filter(action_status)
    case_status = _validate_case_status_filter(case_status)
    lifecycle_stage = _validate_lifecycle_stage_filter(lifecycle_stage)
    aging_dependency = _validate_aging_dependency_filter(aging_dependency)
    priority_form = _validate_priority_form_filter(priority_form)
    _require_priority_access(priority_form, user)
    if agent:
        number = None
        extension = None
    start = parse_call_recording_date(start_date) if start_date else None
    end = parse_call_recording_date(end_date) if end_date else None
    _reject_oversized_date_range(start, end)
    if not start and not end:
        end = date.today()
        start = end - timedelta(days=MAX_CALLS_DATE_RANGE_DAYS)
    elif not start:
        start = end - timedelta(days=MAX_CALLS_DATE_RANGE_DAYS)
    elif not end:
        end = start + timedelta(days=MAX_CALLS_DATE_RANGE_DAYS)
    from services.analysis_read_query import analysis_read
    days = analysis_read(
        db, start=start, end=end, number=number, extension=extension, agent=agent,
        call_direction=call_direction, client_id=client_id, client_number=client_number,
        call_id=call_id, lead_id=lead_id, call_kind=call_kind, review_status=review_status,
        target_page=target_page, open_cases=open_cases, action_status=action_status, case_status=case_status, lifecycle_stage=lifecycle_stage, aging_dependency=aging_dependency, aging_days=aging_days, priority_form=priority_form,
        follow_up_dates_only=True,
    )
    return {"dates": [day.isoformat() for day in days]}


@router.get("/dashboard/performance-analysis")
def get_dashboard_performance_analysis(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return global Dashboard performance rows for every FlintDesk role."""
    start = parse_call_recording_date(start_date) if start_date else None
    end = parse_call_recording_date(end_date) if end_date else None
    query = db.query(CallAnalysisResult)
    # This used to be an unconditional .all() — a full table scan on every
    # single load of the Dashboard. Narrow it in SQL using the indexed
    # normalized date, same OR-NULL pattern as get_analysis above, so legacy
    # rows with no normalized date are still retained for the Python
    # fallback parse below without forcing every other row to be pulled too.
    if start and end:
        query = query.filter(or_(
            CallAnalysisResult.call_recording_date_normalized >= start,
            CallAnalysisResult.call_recording_date_normalized.is_(None),
        )).filter(or_(
            CallAnalysisResult.call_recording_date_normalized <= end,
            CallAnalysisResult.call_recording_date_normalized.is_(None),
        ))
    rows = query.all()
    dated_rows = [(row, _effective_analysis_date(row)) for row in rows]
    if start and end:
        dated_rows = [(row, row_date) for row, row_date in dated_rows if row_date and start <= row_date <= end]
    dated_rows.sort(key=lambda pair: pair[1] or date.min, reverse=True)
    return serialize_analysis_rows([row for row, _ in dated_rows], db)


def _missed_calls_page_count(db: Session, digits_only: str, from_date: Optional[date], to_date: Optional[date]) -> int:
    """Missed Calls page entries (tasks of type missed_call) for a client number.

    Number: same last-10-digits match as the Calls page client filter. Date:
    the date the Missed Calls page itself filters on (tasks.get's
    included_by_date: date, date_added, missed_call_date, miscall_added_date);
    undated entries are only counted when no date bound is given.
    """
    from services.read_scope import digits as _digits_expr

    task_date = func.coalesce(
        FDTask.fd_m_tasks_date, FDTask.fd_m_tasks_date_added,
        FDTask.fd_m_tasks_missed_call_date, FDTask.fd_m_tasks_miscall_added_date,
    )
    query = db.query(func.count(FDTask.id)).filter(
        FDTask.task_type == "missed_call",
        func.right(_digits_expr(FDTask.fd_m_tasks_contact_number), 10) == digits_only[-10:],
    )
    if from_date:
        query = query.filter(task_date >= from_date)
    if to_date:
        query = query.filter(task_date <= to_date)
    return int(query.scalar() or 0)


@router.get("/analysis/by-client-phone")
def get_logs_by_client_phone(
    client_phone: str = Query(..., description="Client phone number (any common format)"),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Every call log for one client phone number, optionally within dates.

    Powers the Calls page "All Logs of Phone Number" popup. It reuses the
    Calls page's own query (analysis_read) and row serializers, so the client
    number is matched exactly like the Client Phone Number filter (last 10
    digits of src or dst) and rows have the same shape as /calls/analysis and
    /calls/. Unlike those, dates are optional: the phone number is what keeps
    the query narrow, so the 92-day window does not apply.
    """
    digits_only = re.sub(r"\D", "", client_phone or "")
    if len(digits_only) < 7:
        raise HTTPException(status_code=422, detail="Please enter a valid client phone number.")
    if from_date and to_date and to_date < from_date:
        raise HTTPException(status_code=400, detail="to_date must be on or after from_date")
    start = from_date or date(2000, 1, 1)
    end = to_date or (date.today() + timedelta(days=1))
    from services.analysis_read_query import analysis_read
    rows, calls_by_id, _ = analysis_read(
        db, start=start, end=end, client_number=digits_only,
        target_page="calls", summary_rows=True,
    )
    items = serialize_analysis_rows(rows, db, include_raw=False, calls_by_id=calls_by_id,
                                    include_transcript=False, summary_rows=True)
    # Calls whose analysis has not been produced yet still belong in the log.
    pending, _, _ = analysis_read(
        db, start=start, end=end, client_number=digits_only, call_source=True,
        target_page="calls", only_unanalyzed=True,
    )
    return {
        "items": items,
        "pending_calls": _serialize_call_records(db, pending),
        # How many times this number is on the Missed Calls page (the Calls
        # page hides those automated-attendant legs; they live there).
        "missed_calls_count": _missed_calls_page_count(db, digits_only, from_date, to_date),
    }


@router.get("/analysis-results")
def get_analysis_results(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Fetch ALL records from call_analysis_results table.
    Returns: call_id, call_reference, call_number, call_recording_date, 
             call_duration, LLM analysis fields, quality_score, processing_status, and more.
    NO AUTH REQUIRED - returns flat array.
    """
    # This endpoint backs a compact table. Avoid serializing the large LLM
    # payload for every analysis row when the UI only needs these columns.
    results = db.query(CallAnalysisResult).options(defer(CallAnalysisResult.raw_llm_json)).all()

    # Parse each row's date once and reuse it for both filtering and
    # sorting, instead of re-parsing the same text value twice per row.
    # A single malformed date must not fail the whole endpoint with a 500.
    dated_rows = []
    for row in results:
        try:
            dated_rows.append((row, parse_call_recording_date(row.call_recording_date)))
        except Exception:
            dated_rows.append((row, None))

    if start_date and end_date:
        start = parse_call_recording_date(start_date)
        end = parse_call_recording_date(end_date)

        if start and end:
            dated_rows = [(row, d) for row, d in dated_rows if d and start <= d <= end]

    dated_rows.sort(key=lambda pair: pair[1] or date.min, reverse=True)

    result = serialize_analysis_rows([row for row, _ in dated_rows], db, include_raw=False)
    
    # Log a sample to verify call_reference is included
    if result:
        print(f"[API] Analysis-Results: Returning {len(result)} records with fields: {list(result[0].keys())}")
    
    return result


@router.get("/dashboard/calls-per-agent")
def get_calls_per_agent(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Dashboard: "Number of Calls per Agent".

    Source table:  call_analysis_results
    Agent column:  front_desk_person_name (the only agent-identifying
                    column this schema actually has — see
                    _fetch_filtered_case_rows, which already filters on
                    it for Pending/Success/Business). There is no
                    extname/extension/agent_name column on this model.

    call_recording_date on CallAnalysisResult is stored as free-text in
    mixed formats, so — exactly like every other endpoint in this file
    (get_analysis, get_analysis_results, _fetch_filtered_case_rows) —
    filtering is done in Python via parse_call_recording_date() rather
    than in SQL, to stay consistent with how those dates are already
    parsed everywhere else.

    NULL/blank front_desk_person_name is coalesced to "N/A" so those
    calls are still counted instead of silently vanishing from the
    result.
    """
    start = parse_call_recording_date(start_date) if start_date else None
    end = parse_call_recording_date(end_date) if end_date else None

    query = db.query(CallAnalysisResult)
    # Same fix as /dashboard/performance-analysis: this used to be an
    # unconditional .all() over the whole table on every request.
    if start and end:
        query = query.filter(or_(
            CallAnalysisResult.call_recording_date_normalized >= start,
            CallAnalysisResult.call_recording_date_normalized.is_(None),
        )).filter(or_(
            CallAnalysisResult.call_recording_date_normalized <= end,
            CallAnalysisResult.call_recording_date_normalized.is_(None),
        ))
    rows = query.all()

    dated_rows = [(row, parse_call_recording_date(row.call_recording_date)) for row in rows]

    if start and end:
        dated_rows = [(row, d) for row, d in dated_rows if d and start <= d <= end]

    # Only load the Call rows actually referenced by the (now already
    # date-narrowed) analysis rows, instead of the entire calls table.
    call_ids = {str(row.call_id) for row, _ in dated_rows if row.call_id}
    calls = {
        str(call.call_id): call
        for call in (db.query(Call).filter(Call.call_id.in_(call_ids)).all() if call_ids else [])
    }

    # assignment_agent_name() issues its own query per call — calling it once
    # per row here was an N+1 (one extra DB round trip per analysis row).
    # assignment_agent_names() resolves the same data in one batched query.
    agent_names = assignment_agent_names(db, list(calls.values()))

    counts: dict[str, int] = {}
    for row, _ in dated_rows:
        agent_name = agent_names.get(str(row.call_id), "-")
        counts[agent_name] = counts.get(agent_name, 0) + 1

    calls_per_agent = [
        {"agent": agent_name, "call_count": count}
        for agent_name, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    ]

    print(f"[Dashboard] Calls per Agent: {len(calls_per_agent)} agents, {sum(counts.values())} total calls.")

    return {"calls_per_agent": calls_per_agent}


@router.get("/dashboard/team-performance")
def get_team_performance_data(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    number: Optional[str] = Query(None),
    extension: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return only the fields needed for the shared team ranking calculation."""
    cache_key = (start_date, end_date, number, extension)
    cached = cache_get("team_performance", cache_key)
    if cached is not None:
        return cached

    start = parse_call_recording_date(start_date) if start_date else None
    end = parse_call_recording_date(end_date) if end_date else None
    rows_query = db.query(CallAnalysisResult)
    # The normalized date is indexed. Retain legacy null-normalized rows for
    # the existing Python fallback, but avoid loading every normalized row.
    if start:
        rows_query = rows_query.filter(or_(
            CallAnalysisResult.call_recording_date_normalized >= start,
            CallAnalysisResult.call_recording_date_normalized.is_(None),
        ))
    if end:
        rows_query = rows_query.filter(or_(
            CallAnalysisResult.call_recording_date_normalized <= end,
            CallAnalysisResult.call_recording_date_normalized.is_(None),
        ))
    rows = [
        row for row in rows_query.all()
        if (row_date := _effective_analysis_date(row))
        and (not start or row_date >= start)
        and (not end or row_date <= end)
    ]
    call_ids = {str(row.call_id) for row in rows if row.call_id}
    calls = {
        str(call.call_id): call
        for call in db.query(Call).filter(Call.call_id.in_(call_ids)).all()
    } if call_ids else {}
    # Team ranking is part of the shared admin-style Dashboard view. Only the
    # separate Performance Analysis endpoint is restricted to an agent's own
    # rows.
    allowed_call_ids = None
    requested_extension = str(extension or "").strip().lower()
    front_desk_agent_ids = {
        str(fd_user.id)
        for fd_user in db.query(FDUser).all()
        if _is_front_desk_agent(fd_user)
    }

    agent_details = assignment_agent_details(db, list(calls.values()))
    extensions_by_parent = with_lead_ring_route(_extensions_by_parent_number(db), number) if number else {}
    result = []
    for row in rows:
        if allowed_call_ids is not None and str(row.call_id) not in allowed_call_ids:
            continue
        call = calls.get(str(row.call_id))
        if number and not _call_matches_lead_number(call, number, extensions_by_parent):
            continue
        if requested_extension and str(call.extension if call else "").strip().lower() != requested_extension:
            continue
        assigned_agent_id, assigned_agent_name = agent_details.get(str(row.call_id), ("", "-"))
        if assigned_agent_id not in front_desk_agent_ids:
            continue
        item = {
            column.name: getattr(row, column.name)
            for column in (
                CallAnalysisResult.call_recording_date,
                CallAnalysisResult.call_recording_date_normalized,
                CallAnalysisResult.call_duration,
                CallAnalysisResult.front_desk_person_name,
                CallAnalysisResult.fd_a_case_worker_assigned,
                CallAnalysisResult.fd_a_client_name,
                CallAnalysisResult.fd_a_case_type,
                CallAnalysisResult.fd_a_client_type,
                CallAnalysisResult.fd_a_urgency_flag,
                CallAnalysisResult.fd_a_call_outcome,
                CallAnalysisResult.fd_m_case_status_change,
                CallAnalysisResult.fd_m_self_action_item,
                CallAnalysisResult.fd_m_action_due_date,
                CallAnalysisResult.fd_m_manual_action_status,
                CallAnalysisResult.fd_m_attachment,
                CallAnalysisResult.fd_m_google_review,
                CallAnalysisResult.fd_m_date_entry,
                CallAnalysisResult.action_status,
                CallAnalysisResult.action_date,
                CallAnalysisResult.overall_call_quality_score,
                CallAnalysisResult.business_opportunity,
            )
        }
        item["front_desk_person_name"] = assigned_agent_name
        item["front_desk_agent_id"] = assigned_agent_id
        item["extension"] = call.extension if call else None
        result.append(item)
    cache_set("team_performance", cache_key, result)
    return result


def _assert_analysis_write_access(record: CallAnalysisResult, user: dict, db: Session) -> None:
    """Server-side ownership check for edits addressed by analysis-record ID.

    Analysis IDs are sequential integers, so authentication alone would let any
    FlintDesk agent edit any record. FlintDesk agents (tokens carrying
    ``fd_user_id``) must pass the same call-access rule as the Calls page.
    Accounts from the main AutoFlint ``users`` table are internal staff and
    keep their existing access.
    """
    if not user.get("fd_user_id") and user.get("user_id"):
        return
    try:
        call = db.query(Call).filter(Call.call_id == UUID(str(record.call_id))).first()
    except (ValueError, TypeError):
        call = db.query(Call).filter(Call.call_reference == str(record.call_id)).first()
    if not call:
        raise HTTPException(status_code=403, detail="You do not have access to this call.")
    assert_manual_call_access(call, user, db)


@router.put("/analysis-results/{analysis_id}/follow-up-status")
def update_follow_up_status(
    analysis_id: int,
    payload: FollowUpStatusUpdateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    status = (payload.follow_up_status or "").strip()
    if not status:
        raise HTTPException(status_code=400, detail="follow_up_status is required")

    record = db.query(CallAnalysisResult).filter(CallAnalysisResult.id == analysis_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Analysis record not found")
    _assert_analysis_write_access(record, user, db)

    record.follow_up_status = status.title()
    db.add(record)
    db.commit()
    db.refresh(record)

    return serialize_sqlalchemy(record)


@router.put("/analysis-results/{analysis_id}/voicemail-left-status")
def update_voicemail_left_status(
    analysis_id: int,
    payload: VoicemailLeftStatusUpdateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    value = (payload.voicemail_left_if_no_answer or "").strip().lower()
    if value not in ("yes", "no"):
        raise HTTPException(status_code=400, detail="voicemail_left_if_no_answer must be 'Yes' or 'No'")

    record = db.query(CallAnalysisResult).filter(CallAnalysisResult.id == analysis_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Analysis record not found")
    _assert_analysis_write_access(record, user, db)

    record.voicemail_left_if_no_answer = value.capitalize()
    db.add(record)
    db.commit()
    db.refresh(record)

    return serialize_sqlalchemy(record)


# ==================================================
# Pending Cases / Success Cases APIs
# ==================================================
def _fetch_filtered_case_rows(
    db: Session,
    start_date: Optional[str],
    end_date: Optional[str],
    agent: Optional[str] = None,
    search: Optional[str] = None,
    action_statuses: Optional[set] = None,
    business_opportunity: Optional[str] = None,  # <-- NEW param, used by /business* endpoints only
):
    """
    Shared query + filter + call-matching logic behind Pending Cases,
    Success Cases, and Business (business_opportunity == 'YES'). All of
    them are "the same data source", so all of the date filtering / agent
    filtering / search / business_opportunity filtering / call_time lookup
    lives here ONCE and every endpoint calls this instead of duplicating
    SQL.

    Returns a list of (row, matching_call) tuples, sorted the same way
    Pending Cases already sorts (newest call_recording_date first).
    """
    # Select only the columns Pending/Success/Business actually use instead
    # of loading full ORM rows — avoids pulling raw_llm_json (JSONB, often
    # the largest field per row) and other unused long-text LLM columns
    # (enquiry_details, call_summary, evaluator_notes, etc.) over the wire
    # for every one of these list requests.
    results = db.query(
        CallAnalysisResult.id,
        CallAnalysisResult.call_id,
        CallAnalysisResult.call_number,
        CallAnalysisResult.call_reference,
        CallAnalysisResult.call_recording_date,
        CallAnalysisResult.client_name,
        CallAnalysisResult.front_desk_person_name,
        CallAnalysisResult.action_status,
        CallAnalysisResult.case_type,
        CallAnalysisResult.follow_up_required,
        CallAnalysisResult.follow_up_deadline,
        CallAnalysisResult.follow_up_status,
        CallAnalysisResult.escalation_required,
        CallAnalysisResult.escalation_to,
        CallAnalysisResult.overall_call_quality_score,
        CallAnalysisResult.action_date,
        CallAnalysisResult.remarks,
        CallAnalysisResult.business_opportunity,
        CallAnalysisResult.review_status,
        CallAnalysisResult.review_status_comment,
        CallAnalysisResult.review_status_updated_date,
    ).all()

    # Parse each row's date once and reuse it for both filtering and
    # sorting, instead of re-parsing the same text value twice per row.
    dated_rows = [(row, parse_call_recording_date(row.call_recording_date)) for row in results]

    if start_date and end_date:
        start = parse_call_recording_date(start_date)
        end = parse_call_recording_date(end_date)

        if start and end:
            dated_rows = [(row, d) for row, d in dated_rows if d and start <= d <= end]

    # Optional case-insensitive filter by agent (front_desk_person_name).
    # Unchanged from the original Pending Cases behavior.
    if agent:
        agent_lower = agent.strip().lower()
        dated_rows = [
            (row, d) for row, d in dated_rows
            if row.front_desk_person_name and row.front_desk_person_name.strip().lower() == agent_lower
        ]

    # Success Cases: restrict to the given action_status values (e.g.
    # {"Success - PC", "Success - GE"}). Pending Cases passes None here so
    # its own behavior is completely unaffected.
    if action_statuses:
        dated_rows = [(row, d) for row, d in dated_rows if row.action_status in action_statuses]

    # <-- NEW block. Business page: restrict to calls flagged as a
    # business opportunity by the LLM. Passed as None by every other
    # caller (e.g. /pending, /success) so their behavior is completely
    # unaffected.
    if business_opportunity:
        dated_rows = [(row, d) for row, d in dated_rows if row.business_opportunity == business_opportunity]

    # Free-text search across client name / front desk person / call
    # reference / formatted call id — the "Search" filter both pages need.
    if search:
        search_lower = search.strip().lower()
        if search_lower:
            def matches(row):
                haystacks = [
                    row.client_name,
                    row.front_desk_person_name,
                    row.call_reference,
                    format_call_id(row.call_number),
                ]
                return any(h and search_lower in str(h).lower() for h in haystacks)
            dated_rows = [(row, d) for row, d in dated_rows if matches(row)]

    dated_rows.sort(key=lambda pair: pair[1] or date.min, reverse=True)

    # call_analysis_results has no time column, but calls does — look up
    # each row's matching Call (by call_reference, then call_number, then
    # call_id, mirroring the Call Records page's own fallback order) so the
    # table can show the real call_time.
    references = {row.call_reference for row, _ in dated_rows if row.call_reference}
    numbers = {row.call_number for row, _ in dated_rows if row.call_number is not None}
    call_ids = {str(row.call_id) for row, _ in dated_rows if row.call_id}
    call_filters = []
    if references:
        call_filters.append(Call.call_reference.in_(references))
    if numbers:
        call_filters.append(Call.call_number.in_(numbers))
    if call_ids:
        call_filters.append(cast(Call.call_id, String).in_(call_ids))

    # Fetch only the calls that can match the displayed analysis rows,
    # rather than loading the complete calls table.
    matching_calls = (
        # Call.transcript added here (<-- CHANGED, was not selected
        # before) so the Business table can show it without a second
        # query. Harmless extra column for /pending and /success, which
        # already build their own dicts field-by-field and don't
        # reference matching_call.transcript.
        db.query(Call.call_reference, Call.call_number, Call.call_id, Call.call_time, Call.transcript)
        .filter(or_(*call_filters))
        .all()
        if call_filters else []
    )
    calls_by_reference = {row.call_reference: row for row in matching_calls if row.call_reference}
    calls_by_number = {row.call_number: row for row in matching_calls if row.call_number is not None}
    calls_by_id = {str(row.call_id): row for row in matching_calls}

    def find_matching_call(row):
        if row.call_reference and row.call_reference in calls_by_reference:
            return calls_by_reference[row.call_reference]
        if row.call_number is not None and row.call_number in calls_by_number:
            return calls_by_number[row.call_number]
        if row.call_id and str(row.call_id) in calls_by_id:
            return calls_by_id[str(row.call_id)]
        return None

    return [(row, find_matching_call(row)) for row, _ in dated_rows]


def _base_case_dict(row, matching_call):
    """
    Fields common to both Pending Cases and Success Cases rows, kept
    IDENTICAL to the original Pending Cases response shape so the frontend
    table component can be reused as-is for Success Cases.
    """
    data = {
        "id": row.id,
        "call_id": row.call_id,
        "call_number": row.call_number,
        "call_reference": row.call_reference,
        "call_recording_date": row.call_recording_date,
        "formatted_call_id": format_call_id(row.call_number),
        "client_name": row.client_name,
        "front_desk_person_name": row.front_desk_person_name,
        "action_status": row.action_status,
        "case_type": row.case_type,
        "follow_up_required": row.follow_up_required,
        "follow_up_deadline": row.follow_up_deadline.isoformat() if hasattr(row.follow_up_deadline, "isoformat") else row.follow_up_deadline,
        "follow_up_status": row.follow_up_status,
        "escalation_required": row.escalation_required,
        "escalation_to": row.escalation_to,
        "overall_call_quality_score": row.overall_call_quality_score,
        "action_date": row.action_date.isoformat() if hasattr(row.action_date, "isoformat") else row.action_date,
        "remarks": row.remarks,
    }
    data["call_time"] = matching_call.call_time.isoformat() if matching_call and matching_call.call_time else None
    return data


@router.get("/pending")
def get_pending_cases(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),  # Pending Cases: Agent dropdown filter
    search: Optional[str] = Query(None),  # optional, added alongside Success Cases; None preserves old behavior
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Fetch records for the Pending Cases page (same data source as the
    Dashboard's Follow-up & Escalation Tracker), including the new
    action_status, action_date, and remarks fields.
    NO AUTH REQUIRED - returns flat array. Mirrors get_analysis_results()
    but is kept as its own endpoint so the Dashboard is not affected.
    """
    rows = _fetch_filtered_case_rows(db, start_date, end_date, agent=agent, search=search)
    result = [_base_case_dict(row, matching_call) for row, matching_call in rows]

    # Pending Cases: clearer, dedicated log message
    print(f"[Pending Cases] Returned {len(result)} records.")

    return result


# ==================================================
# Success Cases APIs
# ==================================================
@router.get("/success")
def get_success_cases(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),          # Front Desk Person filter (same as Pending Cases' "agent")
    search: Optional[str] = Query(None),         # Search filter
    page: Optional[int] = Query(None, ge=1),      # Pagination (optional; omit both params to get the full list)
    page_size: Optional[int] = Query(None, ge=1, le=200),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Fetch records for the Success Cases page: same data source and same
    filters as Pending Cases (start/end date, search, front desk person),
    restricted to calls whose action_status is "Success - PC" or
    "Success - GE" (see SUCCESS_ACTION_STATUSES above for why this is an
    OR, not an AND, on a single-value column).
    Response shape matches Pending Cases exactly, with the three review_*
    fields added on top so the existing table can be reused.
    NO AUTH REQUIRED - returns flat array (same convention as /pending).
    """
    rows = _fetch_filtered_case_rows(
        db, start_date, end_date, agent=agent, search=search,
        action_statuses=SUCCESS_ACTION_STATUSES,
    )

    result = []
    for row, matching_call in rows:
        data = _base_case_dict(row, matching_call)
        # Success Cases-only fields. review_status defaults to
        # "Review Not Received" for display when NULL, WITHOUT writing
        # that default back to the database (per spec).
        data["review_status"] = row.review_status or DEFAULT_REVIEW_STATUS
        data["review_status_comment"] = row.review_status_comment
        data["review_status_updated_date"] = (
            row.review_status_updated_date.isoformat()
            if hasattr(row.review_status_updated_date, "isoformat")
            else row.review_status_updated_date
        )
        result.append(data)

    # Optional pagination: only slice if the caller actually asked for it,
    # so existing/simple callers that omit page/page_size keep getting the
    # full list (matches Pending Cases' current, non-paginated behavior).
    total = len(result)
    if page and page_size:
        start_idx = (page - 1) * page_size
        result = result[start_idx:start_idx + page_size]

    print(f"[Success Cases] Returned {len(result)} of {total} records.")

    return result


@router.patch("/success/{analysis_id}/review-status")
def update_success_case_review_status(
    analysis_id: int,
    payload: ReviewStatusUpdateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Update review_status. Whenever it changes, review_status_updated_date
    is automatically set to today's date, per spec.
    """
    status_value = (payload.review_status or "").strip()
    if status_value not in VALID_REVIEW_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"review_status must be one of: {', '.join(sorted(VALID_REVIEW_STATUSES))}",
        )

    record = db.query(CallAnalysisResult).filter(CallAnalysisResult.id == analysis_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Analysis record not found")
    _assert_analysis_write_access(record, user, db)

    # Only touch review_status_updated_date if the value is actually
    # changing, so re-saving the same status doesn't churn the date.
    if record.review_status != status_value:
        record.review_status = status_value
        record.review_status_updated_date = date.today()

    db.add(record)
    db.commit()
    db.refresh(record)

    return serialize_sqlalchemy(record)


@router.patch("/success/{analysis_id}/review-comment")
def update_success_case_review_comment(
    analysis_id: int,
    payload: ReviewCommentUpdateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Update ONLY review_status_comment. Does not touch review_status or
    review_status_updated_date.
    """
    record = db.query(CallAnalysisResult).filter(CallAnalysisResult.id == analysis_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Analysis record not found")
    _assert_analysis_write_access(record, user, db)

    # Allow clearing the comment with an empty string; only skip the field
    # entirely when it's omitted from the request body (None).
    if payload.review_status_comment is not None:
        record.review_status_comment = payload.review_status_comment.strip()

    db.add(record)
    db.commit()
    db.refresh(record)

    return serialize_sqlalchemy(record)


@router.patch("/success/{analysis_id}/review-date")
def update_success_case_review_date(
    analysis_id: int,
    payload: ReviewDateUpdateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Update ONLY review_status_updated_date, for the frontend date picker
    to override the auto-set date if needed. Does not touch review_status
    or review_status_comment.
    """
    record = db.query(CallAnalysisResult).filter(CallAnalysisResult.id == analysis_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Analysis record not found")
    _assert_analysis_write_access(record, user, db)

    record.review_status_updated_date = payload.review_status_updated_date

    db.add(record)
    db.commit()
    db.refresh(record)

    return serialize_sqlalchemy(record)


@router.patch("/pending/{analysis_id}")
def update_pending_case(
    analysis_id: int,
    payload: PendingCaseUpdateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Update ONLY action_status, action_date, and remarks for a
    call_analysis_results record. Does not touch any other columns.
    """
    record = db.query(CallAnalysisResult).filter(CallAnalysisResult.id == analysis_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Analysis record not found")
    _assert_analysis_write_access(record, user, db)

    # Pending Cases: trim whitespace and validate action_status against the
    # fixed dropdown values before saving. action_date is not trimmed.
    if payload.action_status is not None:
        trimmed_status = payload.action_status.strip()
        if trimmed_status not in VALID_ACTION_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid action_status.")
        _record_case_status_change(db, record, record.action_status, trimmed_status)
        record.action_status = trimmed_status
    if payload.action_date is not None:
        record.action_date = payload.action_date
    if payload.remarks is not None:
        record.remarks = payload.remarks.strip()

    db.add(record)
    db.commit()
    db.refresh(record)

    return serialize_sqlalchemy(record)


@router.patch("/manual-fields/{analysis_id}")
def update_manual_fields(
    analysis_id: int,
    payload: ManualFieldsUpdateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Update FlintDesk's agent-entered manual fields (Self Action Item, Action
    Due Date, Action Status, Dependency, Follow Up Dates, No. of Follow Up
    Attempts, Blocker Comment) for a call_analysis_results record. Does not
    touch any other columns.
    """
    record = db.query(CallAnalysisResult).filter(CallAnalysisResult.id == analysis_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Analysis record not found")

    try:
        call = db.query(Call).filter(Call.call_id == UUID(str(record.call_id))).first()
    except (ValueError, TypeError):
        call = db.query(Call).filter(Call.call_reference == str(record.call_id)).first()
    if not call:
        # Do not permit a detached analysis record to become an authorization
        # bypass for a caller who cannot resolve its underlying call.
        raise HTTPException(status_code=403, detail="You do not have access to this call.")
    assert_manual_call_access(call, user, db)

    if payload.fd_m_self_action_item is not None:
        record.fd_m_self_action_item = payload.fd_m_self_action_item.strip()
    if payload.fd_m_action_due_date is not None:
        record.fd_m_action_due_date = payload.fd_m_action_due_date
    if payload.fd_m_manual_action_status is not None:
        record.fd_m_manual_action_status = payload.fd_m_manual_action_status.strip()
    if payload.fd_m_dependency is not None:
        record.fd_m_dependency = payload.fd_m_dependency.strip()
    if payload.fd_m_follow_up_dates is not None:
        record.fd_m_follow_up_dates = payload.fd_m_follow_up_dates.strip()
    if payload.fd_m_follow_up_attempts is not None:
        record.fd_m_follow_up_attempts = payload.fd_m_follow_up_attempts
    if payload.fd_m_blocker_comment is not None:
        record.fd_m_blocker_comment = payload.fd_m_blocker_comment.strip()
    if payload.fd_m_case_status_change is not None:
        value = accepted_status(payload.fd_m_case_status_change, record.fd_m_case_status_change)
        if value is None:
            raise HTTPException(status_code=400, detail="Invalid fd_m_case_status_change.")
        if (record.fd_m_case_status_change or "").strip() != value:
            record.fd_m_date_entry = case_status_change_date()
        _record_case_status_change(db, record, record.fd_m_case_status_change, value)
        previous_case_status = record.fd_m_case_status_change
        record.fd_m_case_status_change = value
        # Cross-agent closure sync (see services/case_closure.py); never fails the save.
        from services import case_closure
        db.flush()
        _closure_call = db.query(Call).filter(Call.call_id == uuid.UUID(str(record.call_id))).first()
        if _closure_call is not None:
            case_closure.on_status_saved(db, record, _closure_call, user, previous_case_status, value)
    # fd_m_date_entry (Date of Case Status Change) is system-generated above;
    # a client-supplied value is ignored.
    if payload.fd_m_verification_comment is not None:
        record.fd_m_verification_comment = payload.fd_m_verification_comment.strip()
    if payload.fd_m_attachment not in (None, ""):
        raise HTTPException(status_code=422, detail="Attachments can only be changed through the upload endpoint.")
    if payload.fd_m_google_review is not None:
        record.fd_m_google_review = payload.fd_m_google_review.strip()
    if payload.case_closing_completed is not None:
        assert_completion_access(call, user, db)
        if payload.case_closing_completed:
            missing = missing_stage2_fields(record)
            if missing:
                raise HTTPException(
                    status_code=409,
                    detail="Complete all Stage 2 fields before marking this call completed: " + ", ".join(missing),
                )
        record.case_closing_completed = payload.case_closing_completed
        record.case_closing_completed_at = datetime.now(timezone.utc) if payload.case_closing_completed else None

    supplied_fields = getattr(payload, "model_fields_set", getattr(payload, "__fields_set__", set()))
    if supplied_fields.intersection(STAGE2_REQUIRED_FIELDS) and missing_stage2_fields(record):
        record.case_closing_completed = False
        record.case_closing_completed_at = None
    if supplied_fields.intersection(MANUAL_SCORE_FIELDS):
        record.manual_data_score = None
        record.manual_data_score_reason = None
        record.manual_data_scored_at = None

    db.add(record)
    db.commit()
    db.refresh(record)

    return serialize_sqlalchemy(record)


MANUAL_SCORE_FIELDS = (
    "fd_m_self_action_item", "fd_m_action_due_date", "fd_m_manual_action_status",
    "fd_m_dependency", "fd_m_follow_up_dates", "fd_m_follow_up_attempts",
    "fd_m_blocker_comment", "fd_m_case_status_change", "fd_m_date_entry",
    "fd_m_verification_comment", "fd_m_attachment", "fd_m_stage4_attachment",
    "fd_m_google_review",
)


@router.post("/analysis/{analysis_id}/manual-data-score")
def score_manual_data(
    analysis_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Score only human-entered Stage 2–4 data using the configured LLM."""
    record = db.get(CallAnalysisResult, analysis_id)
    if not record:
        raise HTTPException(status_code=404, detail="Analysis record not found")
    manual_data = {
        field: (value.isoformat() if hasattr(value, "isoformat") else value)
        for field in MANUAL_SCORE_FIELDS
        if (value := getattr(record, field, None)) not in (None, "")
    }
    if not manual_data:
        record.manual_data_score = 0
        record.manual_data_score_reason = "No Stage 2, 3, or 4 manual data has been entered yet."
        record.manual_data_scored_at = datetime.now(timezone.utc)
        db.commit(); db.refresh(record)
        return {"score": 0.0, "reason": record.manual_data_score_reason, "scored_at": record.manual_data_scored_at.isoformat()}
    if not LLM_API_KEY or not LLM_URL:
        raise HTTPException(status_code=503, detail="LLM scoring is not configured on the server")
    prompt = f"""
Score the QUALITY of this manually entered FlintDesk Stage 2, 3, and 4 case data from 0 to 10.
The goal is not to reward long answers -- it is to reward information that is useful, specific,
complete, and actionable for the next person who picks up this case.

Judge each entry against these dimensions together, not separately:
- Completeness: does it cover what actually happened/needs to happen, or is something obviously missing?
- Specificity: does it name the actual topic, document, person, or issue -- not just a category?
- Relevance: does it directly relate to this case's current status?
- Actionability: could the next person act on this immediately, or would they have to go ask what it means?
- Context: does it explain why, with whom, and what outcome is expected?

BAD (vague, low score) example: "Will take follow up"
  -- Follow up about what? With whom? Why? What needs to happen? When?
GOOD (specific, high score) example: "Follow up with the client regarding the missing income
  documents on September 20 and confirm whether the documents have been submitted."

Use this fixed quality scale when picking the score:
- 1 to 3 = Below Average (vague, missing key details, not usable as-is)
- 3 to 6 = Average (covers the basics but is generic or incomplete)
- 7 to 8 = Good (mostly specific and actionable, only minor gaps)
- 8 to 10 = Excellent (fully specific, complete, and immediately actionable)

Be lenient and practical, not overly strict. Fields that are genuinely not applicable (e.g. an
empty blocker comment when nothing is blocked) should not be penalized, and borderline entries
should get the benefit of the doubt rather than defaulting to a low score. Still penalize vague,
generic, incomplete, or contradictory entries even if every field is technically filled in.
Evaluate only the supplied manual data; do not invent missing facts.

Return only JSON: {{"score": number, "reason": "one short sentence explaining why the entry earned
that score against the scale above"}}.

MANUAL DATA:
{json.dumps(manual_data, default=str, indent=2)}
""".strip()
    try:
        response = requests.post(
            LLM_URL,
            headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0, "response_format": {"type": "json_object"}},
            timeout=120,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(content)
        score = round(max(0.0, min(10.0, float(result["score"]))), 1)
        reason = str(result.get("reason") or "Manual data was scored using the configured rubric.").strip()
    except (
        requests.RequestException,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        # Anything the LLM provider can return that doesn't match the
        # expected shape (empty choices, a non-JSON body, a missing score
        # field, etc.) must surface as a clean 502 for the frontend to show,
        # never as an unhandled 500.
        raise HTTPException(status_code=502, detail="Unable to score manual data with the LLM") from exc
    record.manual_data_score = score
    record.manual_data_score_reason = reason[:1000]
    record.manual_data_scored_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(record)
    return {"score": score, "reason": record.manual_data_score_reason, "scored_at": record.manual_data_scored_at.isoformat()}


# ==================================================
# Business APIs  <-- NEW SECTION (everything below to the next
# "==== " header is newly added; nothing existing was removed)
# ==================================================
# serial_number, front_desk_person_name, transcript, and audio are added
# on top of the shared "case" fields below, per the Business table spec.
BUSINESS_SORT_FIELDS = {
    "call_date": lambda row, call: (parse_call_recording_date(row.call_recording_date) or date.min),
    "call_number": lambda row, call: (row.call_number if row.call_number is not None else -1),
    "client_name": lambda row, call: (row.client_name or "").lower(),
    "front_desk_person_name": lambda row, call: (row.front_desk_person_name or "").lower(),
    "status": lambda row, call: (row.action_status or "").lower(),
}


@router.get("/business/summary")
def get_business_summary(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    KPI data for the Business page. Reuses the same filtered-row helper
    as Pending Cases / Success Cases, restricted to
    business_opportunity == 'YES', so the KPI numbers and the /business
    table below always agree.

    - business_opportunities: count of all business_opportunity == 'YES' calls
      matching the applied filters.
    - converted_to_paid_consultation: of those, count where
      action_status == 'Success - PC'.
    - pending_business_opportunities: business_opportunities -
      converted_to_paid_consultation (computed here, not on the frontend).
    """
    rows = _fetch_filtered_case_rows(
        db, start_date, end_date, agent=agent, search=search,
        business_opportunity=BUSINESS_OPPORTUNITY_YES,
    )

    business_opportunities = len(rows)
    converted_to_paid_consultation = sum(
        1 for row, _ in rows if row.action_status == PAID_CONSULTATION_STATUS
    )
    pending_business_opportunities = business_opportunities - converted_to_paid_consultation

    return {
        "business_opportunities": business_opportunities,
        "converted_to_paid_consultation": converted_to_paid_consultation,
        "pending_business_opportunities": pending_business_opportunities,
    }


@router.get("/business")
def get_business_calls(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),          # Front Desk Person filter
    search: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("call_date", json_schema_extra={"enum": list(BUSINESS_SORT_FIELDS.keys())}),
    sort_order: Optional[str] = Query("desc", json_schema_extra={"enum": ["asc", "desc"]}),
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=200),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Business table: ONLY calls where business_opportunity == 'YES'.
    Reuses the same filtering (date range, front desk person / agent,
    search) and pagination conventions as Pending Cases / Success Cases,
    plus column sorting and a serial_number for the table.

    Returns: serial_number, call_number (+ formatted_call_id), client_name,
    call_date, call_time, call_id, front_desk_person_name, transcript,
    audio, status.
    """
    rows = _fetch_filtered_case_rows(
        db, start_date, end_date, agent=agent, search=search,
        business_opportunity=BUSINESS_OPPORTUNITY_YES,
    )

    # rows is already sorted newest-first by call_recording_date; re-sort
    # only when the caller asked for a different column/order so the
    # default response shape is unchanged.
    sort_key = BUSINESS_SORT_FIELDS.get(sort_by, BUSINESS_SORT_FIELDS["call_date"])
    if sort_by != "call_date" or sort_order != "desc":
        rows = sorted(
            rows,
            key=lambda pair: sort_key(pair[0], pair[1]),
            reverse=(sort_order == "desc"),
        )

    total = len(rows)

    if page and page_size:
        start_idx = (page - 1) * page_size
        rows = rows[start_idx:start_idx + page_size]
        first_serial = start_idx + 1
    else:
        first_serial = 1

    result = []
    for offset, (row, matching_call) in enumerate(rows):
        call_id = matching_call.call_id if matching_call else row.call_id
        result.append({
            "serial_number": first_serial + offset,
            "call_number": row.call_number,
            "formatted_call_id": format_call_id(row.call_number),
            "client_name": row.client_name,
            "call_date": row.call_recording_date,
            "call_time": matching_call.call_time.isoformat() if matching_call and matching_call.call_time else None,
            "call_id": str(call_id) if call_id else None,
            "front_desk_person_name": row.front_desk_person_name,
            "transcript": matching_call.transcript if matching_call else None,
            "audio": f"/calls/audio?call_id={call_id}" if call_id else None,
            "status": row.action_status,
        })

    print(f"[Business] Returned {len(result)} of {total} records.")

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "results": result,
    }


# ==================================================
# Client FAQ API  <-- NEW SECTION
# ==================================================
@router.get("/client-faq")
def get_client_faq(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Client FAQ table: every analysis row where the LLM captured a
    client_question, newest first. Backs a simple Sr No / Date / Call Ref
    / Question table — filters are date range + free-text search over the
    question itself.
    """
    rows = (
        db.query(
            CallAnalysisResult.id,
            CallAnalysisResult.call_number,
            CallAnalysisResult.call_reference,
            CallAnalysisResult.call_recording_date,
            CallAnalysisResult.client_question,
        )
        .filter(CallAnalysisResult.client_question.isnot(None))
        .filter(CallAnalysisResult.client_question != "")
        .all()
    )

    dated_rows = [(row, parse_call_recording_date(row.call_recording_date)) for row in rows]

    if start_date and end_date:
        start = parse_call_recording_date(start_date)
        end = parse_call_recording_date(end_date)
        if start and end:
            dated_rows = [(row, d) for row, d in dated_rows if d and start <= d <= end]

    search_term = (search or "").strip().lower()
    if search_term:
        dated_rows = [
            (row, d) for row, d in dated_rows
            if search_term in (row.client_question or "").lower()
            or search_term in (row.call_reference or "").lower()
        ]

    dated_rows.sort(key=lambda pair: pair[1] or date.min, reverse=True)

    result = [
        {
            "id": row.id,
            "serial_number": idx + 1,
            "call_date": row.call_recording_date,
            "call_reference": row.call_reference,
            "formatted_call_id": format_call_id(row.call_number),
            "question": row.client_question,
        }
        for idx, (row, _) in enumerate(dated_rows)
    ]

    return result


def _get_call_or_raise(call_id: str, db: Session):
    try:
        call = db.query(Call).filter(Call.call_id == call_id).first()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Audio service is temporarily unavailable because the database connection failed."
        ) from exc

    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    return call


def _is_gsm_wav(path: str) -> bool:
    with open(path, "rb") as f:
        header = f.read(64)
    if len(header) < 24 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        return False

    offset = 12
    while offset + 8 <= len(header):
        chunk_id = header[offset:offset+4]
        chunk_size = int.from_bytes(header[offset+4:offset+8], "little")
        if chunk_id == b"fmt ":
            if offset + 8 + 2 <= len(header):
                fmt_tag = int.from_bytes(header[offset+8:offset+10], "little")
                return fmt_tag == 49
            return False
        offset += 8 + chunk_size
    return False


def _normalize_audio(input_path: str, output_path: str):
    ffmpeg_exe = get_ffmpeg_exe()
    if not ffmpeg_exe or not Path(ffmpeg_exe).exists():
        raise RuntimeError("FFmpeg executable not found for audio conversion.")

    command = [
        ffmpeg_exe,
        "-y",
        "-i", input_path,
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        output_path,
    ]
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        raise RuntimeError(f"Audio conversion failed: {process.stderr.strip()}")


@router.get("/audio")
def get_audio(
    call_id: str = Query(...),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    call = _get_call_or_raise(call_id, db)

    audio_ref = call.audio_s3_uri or call.audio_s3_key
    if not audio_ref:
        raise HTTPException(status_code=404, detail="Audio not available for this call")

    try:
        s3_service = S3Service()
        body, content_type = s3_service.get_object_stream(audio_ref)
    except (NoCredentialsError, PartialCredentialsError):
        raise HTTPException(
            status_code=503,
            detail="AWS credentials are not configured on the backend. Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and AWS_REGION in the backend environment."
        )
    except Exception as exc:
        logging.getLogger(__name__).exception("Failed to stream audio")
        raise HTTPException(status_code=500, detail="Failed to stream audio.")

    # Normalize GSM WAV to standard PCM WAV if needed.
    temp_dir = tempfile.mkdtemp()
    source_path = os.path.join(temp_dir, "source_audio.wav")
    target_path = os.path.join(temp_dir, "normalized_audio.wav")

    try:
        with open(source_path, "wb") as f:
            f.write(body.read())

        if _is_gsm_wav(source_path):
            try:
                _normalize_audio(source_path, target_path)
                stream_path = target_path
                media_type = "audio/wav"
            except Exception as exc:
                logging.getLogger(__name__).exception("Audio normalization failed")
                raise HTTPException(status_code=500, detail="Audio normalization failed.")
        else:
            stream_path = source_path
            media_type = content_type or "audio/wav"

        file_handle = open(stream_path, "rb")

        def cleanup():
            try:
                file_handle.close()
            except Exception:
                pass
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

        return StreamingResponse(
            iter(lambda: file_handle.read(1024 * 1024), b""),
            media_type=media_type,
            headers={"Cache-Control": "no-store", "Accept-Ranges": "bytes"},
            background=BackgroundTask(cleanup),
        )
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


@router.get("/audio-url")
def get_audio_url(
    call_id: str = Query(...),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    call = _get_call_or_raise(call_id, db)

    audio_ref = call.audio_s3_uri or call.audio_s3_key
    if not audio_ref:
        raise HTTPException(status_code=404, detail="Audio not available for this call")

    return {"url": f"/calls/audio?call_id={call_id}", "expires_in": 300, "call_id": str(call.call_id)}
