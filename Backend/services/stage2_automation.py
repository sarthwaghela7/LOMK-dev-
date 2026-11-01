"""Stage 2 AI automation: prompt assembly, strict validation and safe persistence.

Everything the model returns is untrusted. The backend alone decides what is
stored, independently of the prompt text:

* Action Due Date is date-only, so "strictly less than 72 hours after entry" is
  enforced at day granularity: a date D is allowed only if the END of day D
  (local business time) is still before entry + 72h. That is entry date + 2
  calendar days at most, never +3. Earlier-than-entry dates are raised to the
  entry date. Entry time is calls.registered_at (America/Chicago).
* Follow Up Dates holds a client-response follow-up only when the model says a
  client response is pending; otherwise the canonical text "N/A" is stored (the
  column is Text and part of the required Stage 2 gate, so NULL would block Stage 3).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger("flintdesk.stage2")

BUSINESS_TZ = ZoneInfo("America/Chicago")
UTC = timezone.utc
MAX_ACTION_ITEM_CHARS = 2000
MAX_NOTE_CHARS = 500
MAX_FOLLOW_UP_DAYS = 30
FOLLOW_UP_FALLBACK_DAYS = 3
DUE_FALLBACK_DAYS = 1
NO_FOLLOW_UP = "N/A"
NO_ACTION_TEXT = "No further front-desk action required."
STAGE2_FIELDS = ("fd_m_self_action_item", "fd_m_action_due_date", "fd_m_follow_up_dates")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_GENERIC = {
    "follow up with the client", "contact the client", "check the case", "handle the inquiry",
    "take necessary action", "follow up", "follow up with client", "contact client",
    "follow up with the client regarding their immigration inquiry",
}

OUTPUT_CONTRACT = (
    "Return ONLY a JSON object with exactly these keys: "
    '"self_action_item" (string or null), "no_action_required" (boolean), '
    '"action_due_date" (YYYY-MM-DD or null), "client_response_pending" (boolean), '
    '"follow_up_date" (YYYY-MM-DD or null), "follow_up_note" (string or null), '
    '"reasoning_summary" (one short factual sentence or null). '
    "Use only information in the transcript and call context; never invent facts."
)


def audit_event(*args, **kwargs):
    """Record an audit event when the audit service exists; never raises."""
    try:
        from services.audit_service import audit
    except ImportError:
        return False
    return audit(*args, **kwargs)


# ---------------------------------------------------------------- dates
def entry_datetime(call) -> datetime | None:
    """Authoritative record-entry time in business timezone (calls.registered_at)."""
    registered = getattr(call, "registered_at", None)
    if registered is not None:
        if registered.tzinfo is None:
            registered = registered.replace(tzinfo=BUSINESS_TZ)
        return registered.astimezone(BUSINESS_TZ)
    call_date, call_time = getattr(call, "call_date", None), getattr(call, "call_time", None)
    if call_date is None:
        return None
    return datetime.combine(call_date, call_time or dtime(0, 0), tzinfo=BUSINESS_TZ)


def _end_of_day_utc(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), dtime(0, 0), tzinfo=BUSINESS_TZ).astimezone(UTC) - timedelta(seconds=1)


def max_action_due_date(entry: datetime) -> date:
    """Latest date whose end-of-day is strictly less than entry + 72h."""
    limit = entry.astimezone(UTC) + timedelta(hours=72)
    day = entry.date() + timedelta(days=3)
    while day > entry.date() and not _end_of_day_utc(day) < limit:
        day -= timedelta(days=1)
    return day


def parse_iso_date(value) -> date | None:
    if not isinstance(value, str) or not _ISO_DATE.match(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def resolve_due_date(raw, entry: datetime, has_action: bool = True):
    """(date|None, status). The result never exceeds max_action_due_date(entry)."""
    if not has_action:
        return None, "no_action"
    entry_day, latest = entry.date(), max_action_due_date(entry)
    parsed = parse_iso_date(raw)
    if parsed is None:
        logger.warning("stage2: invalid action_due_date from model; using deterministic fallback")
        return min(entry_day + timedelta(days=DUE_FALLBACK_DAYS), latest), "fallback"
    if parsed > latest:
        logger.warning("stage2: action_due_date beyond 72h limit; clamped")
        return latest, "clamped"
    if parsed < entry_day:
        logger.warning("stage2: action_due_date before entry date; raised")
        return entry_day, "clamped"
    return parsed, "ok"


def resolve_follow_up(pending, raw, entry: datetime):
    """(MM-DD-YYYY | 'N/A', status). A date is only ever kept when pending is True."""
    if pending is not True:
        return NO_FOLLOW_UP, "none_pending"
    entry_day = entry.date()
    earliest, latest = entry_day + timedelta(days=1), entry_day + timedelta(days=MAX_FOLLOW_UP_DAYS)
    parsed = parse_iso_date(raw)
    if parsed is None:
        logger.warning("stage2: pending client response without valid follow_up_date; fallback")
        return (entry_day + timedelta(days=FOLLOW_UP_FALLBACK_DAYS)).strftime("%m-%d-%Y"), "fallback"
    status = "ok"
    if parsed < earliest:
        parsed, status = earliest, "clamped"
    elif parsed > latest:
        parsed, status = latest, "clamped"
    return parsed.strftime("%m-%d-%Y"), status


# ---------------------------------------------------------------- text
def validate_action_item(value) -> str | None:
    if not isinstance(value, str):
        return None
    text_value = re.sub(r"[ \t]+", " ", value).strip()
    if not text_value:
        return None
    if text_value.rstrip(". ").lower() in _GENERIC:
        return None
    return text_value[:MAX_ACTION_ITEM_CHARS]


def _clean_note(value) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:MAX_NOTE_CHARS] or None


def build_stage2_block(prompts: dict, call, entry: datetime) -> str:
    """Stage 2 section appended to the Stage 1 request (one request per call)."""
    latest = max_action_due_date(entry)
    values = {
        "entry_datetime": entry.isoformat(), "entry_date": entry.date().isoformat(),
        "max_action_due_date": latest.isoformat(), "transcript": "(the CALL TRANSCRIPT above)",
    }
    keys = ("stage2_self_action_item", "action_due_date", "follow_up_date")
    sections = "\n\n".join(
        f"### {key}\n{prompts[key]['system_prompt']}\n{_fill(prompts[key]['user_prompt_template'], values)}"
        for key in keys
    )
    return (
        "ADDITIONAL TASK - FRONT-DESK ACTION PLAN\n"
        f"The call entered FlintDesk at {entry.isoformat()} (America/Chicago). Entry date: {entry.date().isoformat()}. "
        f"Latest allowed Action Due Date: {latest.isoformat()}.\n\n{sections}\n\n"
        "For this task ONLY, add ONE extra top-level key named \"stage2\" to the JSON object (this overrides the "
        "'no extra keys' rule for that key alone). Its value must be an object. "
        + OUTPUT_CONTRACT.replace("Return ONLY a JSON object with", "The stage2 object has")
    )


def extract_stage2(content, call) -> dict | None:
    """Validated Stage 2 result from a combined response's content, or None. Never raises."""
    try:
        entry = entry_datetime(call)
        parsed = parse_model_json(content)
        nested = parsed.get("stage2") if parsed else None
        return resolve_result(nested, entry) if entry and isinstance(nested, dict) else None
    except Exception:  # noqa: BLE001 - Stage 2 must never break Stage 1
        logger.warning("stage2: could not extract result for call %s", getattr(call, "call_id", None))
        return None


# ---------------------------------------------------------------- prompt/response
def _fill(template: str, values: dict) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template


def build_messages(prompts: dict, transcript: str, call, entry: datetime) -> list[dict]:
    """One request carrying the three configurable prompts plus the fixed output contract."""
    latest = max_action_due_date(entry)
    metadata = {
        "call_id": str(getattr(call, "call_id", "")), "direction_src": getattr(call, "src", None),
        "direction_dst": getattr(call, "dst", None), "duration_seconds": getattr(call, "billsec", None),
        "disposition": getattr(call, "disposition", None), "agent": getattr(call, "extname", None),
    }
    values = {
        "entry_datetime": entry.isoformat(), "entry_date": entry.date().isoformat(),
        "max_action_due_date": latest.isoformat(), "transcript": "(see CALL TRANSCRIPT above)",
    }
    system = "\n\n".join(
        f"### {key}\n{prompts[key]['system_prompt']}"
        for key in ("stage2_self_action_item", "action_due_date", "follow_up_date")
    ) + "\n\n### OUTPUT FORMAT\n" + OUTPUT_CONTRACT
    user = (
        f"CALL CONTEXT:\nEntered FlintDesk (America/Chicago): {entry.isoformat()}\n"
        f"Entry date: {entry.date().isoformat()}\nLatest allowed Action Due Date: {latest.isoformat()}\n"
        f"Call metadata: {json.dumps(metadata, default=str)}\n\nCALL TRANSCRIPT:\n{transcript}\n\n"
        + "\n\n".join(_fill(prompts[key]["user_prompt_template"], values)
                      for key in ("stage2_self_action_item", "action_due_date", "follow_up_date"))
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_model_json(content) -> dict | None:
    if not isinstance(content, str):
        return None
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def resolve_result(parsed: dict | None, entry: datetime) -> dict | None:
    """Validate a parsed model object. None means unusable (caller may retry)."""
    if not isinstance(parsed, dict):
        return None
    if parsed.get("no_action_required") is True:
        item, due, due_status = NO_ACTION_TEXT, None, "no_action"
        follow_up, follow_status = NO_FOLLOW_UP, "none_pending"
        note = None
    else:
        item = validate_action_item(parsed.get("self_action_item"))
        if item is None:
            return None
        due, due_status = resolve_due_date(parsed.get("action_due_date"), entry)
        follow_up, follow_status = resolve_follow_up(parsed.get("client_response_pending"),
                                                     parsed.get("follow_up_date"), entry)
        note = _clean_note(parsed.get("follow_up_note")) if follow_up != NO_FOLLOW_UP else None
    return {
        "fd_m_self_action_item": item,
        "fd_m_action_due_date": due.isoformat() if due else None,
        "fd_m_follow_up_dates": follow_up,
        "follow_up_note": note,
        "dependency_client": follow_up != NO_FOLLOW_UP,
        "due_status": due_status, "follow_up_status": follow_status,
    }


# ---------------------------------------------------------------- persistence
def _same(current, recorded) -> bool:
    return str(current.isoformat() if hasattr(current, "isoformat") else current or "").strip() == str(recorded or "").strip()


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip().casefold() in {"", "-", "n/a", "na", "none"})


def apply_stage2_result(analysis, result: dict, prompt_versions: dict, *, replace_manual: bool = False) -> list[str]:
    """Write validated values; never overwrite an agent's value unless it is unchanged AI output.

    Returns the list of fields written. `replace_manual=True` is the explicit
    regeneration opt-in to also replace values an agent typed.
    """
    meta = dict(analysis.fd_stage2_ai_meta or {})
    stamp = datetime.now(UTC).isoformat()
    written = []
    values = {
        "fd_m_self_action_item": result["fd_m_self_action_item"],
        "fd_m_action_due_date": result["fd_m_action_due_date"],
        "fd_m_follow_up_dates": result["fd_m_follow_up_dates"],
    }
    for field, value in values.items():
        current = getattr(analysis, field, None)
        recorded = (meta.get(field) or {}).get("value")
        replaceable = replace_manual or _is_blank(current) or (field in meta and _same(current, recorded))
        if not replaceable:
            continue
        if field == "fd_m_action_due_date":
            setattr(analysis, field, date.fromisoformat(value) if value else None)
        else:
            setattr(analysis, field, value)
        meta[field] = {"source": "ai", "value": value, "generated_at": stamp, **prompt_versions}
        written.append(field)
    if result.get("follow_up_note") and "fd_m_follow_up_dates" in written and _is_blank(analysis.fd_m_action_due_note):
        analysis.fd_m_action_due_note = "Follow-up: " + result["follow_up_note"]
    if result.get("dependency_client") and "fd_m_follow_up_dates" in written and _is_blank(analysis.fd_m_dependency):
        analysis.fd_m_dependency = "Client"
    analysis.fd_stage2_ai_meta = meta
    return written
