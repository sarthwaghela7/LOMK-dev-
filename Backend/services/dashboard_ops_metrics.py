"""Dashboard operational metrics: assignment/callback table, Close Call Aging
and Client Conversion Duration.

Every rule here mirrors an existing page rather than inventing one:

* Leads / Missed Calls / Non-Responsive / SM Leads "Called Back" counts the rows
  that are *green* on that page (see the per-block docstrings for the exact
  stored condition), and "Assigned" counts rows with an owner AND a submitted
  assignment.
* Calls population and the Agent / Number / Extension filters are a Python port
  of ``computeDashboardSummary``'s ``filteredCalls`` so the new cards use the
  same calls as Open Call Aging.

The deployed Node calculator is intentionally not touched (it is not
reproducible from the current frontend source), so these values are added to the
summary response next to it. All counting is over rows the summary already
loaded plus one SM-Leads query and one status-history query.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import Date, and_, case, cast, func, text

logger = logging.getLogger("autoflint")
CENTRAL = ZoneInfo("America/Chicago")
AGING_THRESHOLDS = (3, 5, 10, 15)
DURATION_THRESHOLDS_MINUTES = (3, 5, 10, 15)

# Mirrors FlintDesk/src/data/leadNumberFilter.js.
_LEAD_NUMBER_GROUPS = {
    "3128781255": {"routing": ("601",), "extensions": ("101", "102", "255")},
    "2243374447": {"routing": ("603",), "extensions": ("201", "202", "4447")},
    "2243939098": {"routing": ("605",), "extensions": ("203", "204", "205")},
}
_ALIAS_EXTENSION = {"3128781255": "255", "2243374447": "4447"}
_NON_SELECTABLE = {"601", "603", "605", "255", "4447"}


def normalize_routing_number(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def lead_number_extensions(lead_number) -> set[str]:
    normalized = normalize_routing_number(lead_number)
    group = _LEAD_NUMBER_GROUPS.get(normalized)
    if not group:
        return set()
    extensions = {e for e in group["extensions"] if e not in _NON_SELECTABLE}
    if normalized in _ALIAS_EXTENSION:
        extensions.add(normalized)
    return extensions


# --------------------------------------------------------------------------
# Assignment / called-back table
# --------------------------------------------------------------------------

def _block(assigned: int, called_back: int) -> dict:
    return {"assigned": assigned, "calledBack": called_back}


def leads_block(leads, agent="all", number="", extension="") -> dict:
    """Leads page. Assigned: owner persisted by Submit (``has_submitted_assignment``;
    ``submitted_at`` is not a stored lead column). Green: the page's own
    ``same_day_outbound_call_id`` (outbound call on the assignment day)."""
    permitted = lead_number_extensions(number) if number else None
    seen, assigned, called_back = set(), 0, 0
    for item in leads or []:
        row_id = item.get("id")
        if row_id in seen:
            continue
        record = item.get("record") or {}
        ext = str(record.get("extension") or (item.get("call_details") or {}).get("extension") or "")
        owner = str(record.get("owner") or record.get("agent_name") or "").strip()
        if agent != "all" and owner != agent:
            continue
        if permitted is not None and ext not in permitted:
            continue
        if extension and ext != extension:
            continue
        seen.add(row_id)
        assigned += bool(item.get("has_submitted_assignment"))
        called_back += bool(item.get("same_day_outbound_call_id"))
    return _block(assigned, called_back)


def tasks_block(tasks, task_type: str, agent="all") -> dict:
    """Missed Calls / Non-Responsive pages. Assigned: owner AND ``submitted_at``
    (an imported "Contacted by" owner alone is not an assignment). Green:
    Missed Calls -> a matched outbound callback (``callback`` / ``callback_matched``);
    Non-Responsive -> the row is matched to a real call (``data.call_id``), exactly
    how UnansweredCalls.jsx colours it. Dashboard filter semantics follow the
    existing task summary: only Agent applies."""
    wanted = agent.strip().lower() if agent != "all" else ""
    seen, assigned, called_back = set(), 0, 0
    for row in tasks or []:
        if row.get("task_type") != task_type or row.get("id") in seen:
            continue
        if wanted and str(row.get("owner_name") or "").strip().lower() != wanted:
            continue
        seen.add(row.get("id"))
        data = row.get("data") or {}
        assigned += bool(row.get("owner_id") and row.get("submitted_at"))
        if task_type == "missed_call":
            called_back += bool(data.get("callback") or data.get("callback_matched"))
        else:
            called_back += bool(data.get("call_id"))
    return _block(assigned, called_back)


def sm_leads_block(db, start: date, end: date, agent="all") -> dict:
    """SM Leads page. Assigned: owner + ``submitted_at`` + not Pending. Green:
    ``_call_made`` (outbound call to the client on the Central day of date_added).
    Dates use the page's own Central ``date_added`` day. Number/Extension do not
    apply (SM leads store neither)."""
    from routes.calls import _system_numbers
    from routes.flintdesk.sm_leads import _call_made
    from storage.models import FDSMLead

    made = _call_made(_system_numbers(db))
    added_on = cast(func.timezone("America/Chicago", FDSMLead.date_added), Date)
    assigned_expr = and_(FDSMLead.owner_id.isnot(None), FDSMLead.submitted_at.isnot(None),
                         FDSMLead.status != "Pending")
    query = db.query(
        func.count(func.distinct(case((assigned_expr, FDSMLead.id)))),
        func.count(func.distinct(case((made, FDSMLead.id)))),
    ).filter(added_on.between(start, end))
    if agent != "all":
        query = query.filter(func.lower(func.trim(FDSMLead.owner)) == agent.strip().lower())
    assigned, called_back = query.one()
    return _block(int(assigned or 0), int(called_back or 0))


# --------------------------------------------------------------------------
# Calls population (port of filteredCalls)
# --------------------------------------------------------------------------

def _is_voicemail(raw: dict) -> bool:
    if str(raw.get("voicemail_left_if_no_answer") or "").strip().lower() in {"yes", "true", "1"}:
        return True
    outcome = str(raw.get("fd_a_call_outcome") or raw.get("call_outcome") or "")
    return "voicemail" in outcome.lower()


def build_call_records(analysis_rows, calls) -> list[dict]:
    """One record per call id (analysis row preferred, plus calls with no analysis)."""
    calls_by_id = {c.get("call_id"): c for c in calls or []}
    analysed = {r.get("call_id") for r in analysis_rows or []}
    records, seen = [], set()
    for row in [*(analysis_rows or []), *[c for c in calls or [] if c.get("call_id") not in analysed]]:
        call_id = row.get("call_id")
        if call_id in seen:
            continue
        seen.add(call_id)
        call = calls_by_id.get(call_id) or {}
        records.append({
            "call_id": call_id,
            "analysis_id": row.get("id") if row.get("call_id") in analysed else None,
            "agent": row.get("front_desk_person_name") or call.get("front_desk_person_name") or "-",
            "extension": str(call.get("extension") or row.get("extension") or "-"),
            "src": call.get("src") or row.get("src") or "",
            "dst": call.get("dst") or row.get("dst") or "",
            "duration": row.get("call_duration") or call.get("billsec") or row.get("billsec"),
            "voicemail": _is_voicemail(row),
            "status": row.get("fd_m_case_status_change"),
            "created_at": row.get("created_at"),
        })
    return records


def filter_call_records(records, agent="all", number="", extension="") -> list[dict]:
    expected = normalize_routing_number(number)
    group = _LEAD_NUMBER_GROUPS.get(expected)
    out = []
    for rec in records:
        if agent != "all" and str(rec["agent"]).strip() != agent:
            continue
        if number:
            src, dst = normalize_routing_number(rec["src"]), normalize_routing_number(rec["dst"])
            if not (src == expected or dst == expected or (group and dst in group["routing"])):
                continue
        if extension and rec["extension"] not in (extension, _ALIAS_EXTENSION.get(extension)):
            continue
        out.append(rec)
    return out


# --------------------------------------------------------------------------
# Client Conversion Duration
# --------------------------------------------------------------------------

def parse_duration_seconds(value):
    """Seconds from the Call Records Duration value, or None when invalid.

    The stored column is ``str(billsec)`` (whole seconds); ``m:ss`` / ``h:mm:ss``
    are also accepted. Anything else (blank, "-", negative) is invalid, never 0."""
    if value is None or isinstance(value, bool):
        return None
    text_value = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text_value):
        return float(text_value)
    match = re.fullmatch(r"(?:(\d+):)?(\d{1,3}):([0-5]\d)", text_value)
    if match:
        hours, minutes, seconds = (int(part or 0) for part in match.groups())
        return hours * 3600 + minutes * 60 + seconds
    return None


def conversion_duration(records) -> dict:
    """Distinct non-voicemail calls with Duration >= N minutes (boundary inclusive)."""
    counts = {minutes: 0 for minutes in DURATION_THRESHOLDS_MINUTES}
    eligible = invalid = 0
    for rec in records:
        if rec["voicemail"]:
            continue
        seconds = parse_duration_seconds(rec["duration"])
        if seconds is None:
            invalid += 1
            continue
        eligible += 1
        for minutes in DURATION_THRESHOLDS_MINUTES:
            counts[minutes] += seconds >= minutes * 60
    return {**{str(m): n for m, n in counts.items()}, "eligible": eligible, "invalidDuration": invalid}


# --------------------------------------------------------------------------
# Close Call Aging
# --------------------------------------------------------------------------

def normalize_closed_status(value) -> str | None:
    """Canonical Stage 3 closure status, or None when the status is not one of
    Success GE / Success PC / Dropped. Tolerates case, spacing and hyphenation."""
    cleaned = re.sub(r"\s+", " ", str(value or "").strip().lower())
    if cleaned == "dropped":
        return "dropped"
    match = re.fullmatch(r"success ?-? ?(ge|pc)", cleaned)
    return f"success - {match.group(1)}" if match else None


def _to_central_date(value) -> date | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)  # created_at is stored as UTC wall clock
    return value.astimezone(CENTRAL).date()


# (key, lower bound inclusive, upper bound exclusive) in whole days.
CLOSE_DURATION_BUCKETS = (("under3", 0, 3), ("d3to4", 3, 5), ("d5to9", 5, 10), ("d10to14", 10, 15), ("d15plus", 15, None))


def close_call_aging(records, closed_at_by_key) -> dict:
    """How many closed calls were closed within each time range: whole days from
    entry into FlintDesk (``call_analysis_results.created_at``) to the Stage 3
    transition (``fd_case_status_history.changed_at`` of the current closing
    status). Ranges are mutually exclusive, so they add up to ``eligible``.
    Missing/invalid/reversed dates are excluded and reported, never guessed."""
    counts = {key: 0 for key, _, _ in CLOSE_DURATION_BUCKETS}
    eligible = missing = 0
    for rec in records:
        status = normalize_closed_status(rec["status"])
        if not status:
            continue
        closed = _to_central_date(closed_at_by_key.get((rec["analysis_id"], status)))
        entered = _to_central_date(rec["created_at"])
        if closed is None or entered is None or closed < entered:
            missing += 1
            continue
        eligible += 1
        age = (closed - entered).days
        for key, low, high in CLOSE_DURATION_BUCKETS:
            if age >= low and (high is None or age < high):
                counts[key] += 1
                break
    return {**counts, "eligible": eligible, "excludedMissingDates": missing}


def load_closed_at(db, records) -> dict:
    """{(analysis_id, canonical status): latest changed_at} for closing statuses."""
    ids = sorted({r["analysis_id"] for r in records
                  if r["analysis_id"] is not None and normalize_closed_status(r["status"])})
    result: dict = {}
    for start in range(0, len(ids), 5000):
        rows = db.execute(text(
            "SELECT analysis_id, new_status, MAX(changed_at) FROM fd_case_status_history "
            "WHERE analysis_id = ANY(:ids) GROUP BY analysis_id, new_status"
        ), {"ids": ids[start:start + 5000]}).all()
        for analysis_id, new_status, changed_at in rows:
            status = normalize_closed_status(new_status)
            key = (analysis_id, status)
            if status and (key not in result or changed_at > result[key]):
                result[key] = changed_at
    return result


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def _safe(label, fn):
    try:
        return fn()
    except Exception:  # one failing card must not take the whole Dashboard down
        logger.exception("[dashboard] %s failed", label)
        return {"error": True}


def build_ops_metrics(db, *, start, end, agent, number, extension,
                      leads, tasks, dashboard_analysis, dashboard_calls) -> dict:
    table = {
        "leads": _safe("ops leads", lambda: leads_block(leads, agent, number, extension)),
        "missedCalls": _safe("ops missed", lambda: tasks_block(tasks, "missed_call", agent)),
        "nonResponsiveCalls": _safe("ops non-responsive", lambda: tasks_block(tasks, "non_responsiveness", agent)),
        "smLeads": _safe("ops sm leads", lambda: sm_leads_block(db, start, end, agent)),
    }
    records = filter_call_records(build_call_records(dashboard_analysis, dashboard_calls), agent, number, extension)
    return {
        "operationalCallTable": table,
        "closeCallAging": _safe("close aging", lambda: close_call_aging(records, load_closed_at(db, records))),
        "clientConversionDuration": _safe("conversion duration", lambda: conversion_duration(records)),
    }
