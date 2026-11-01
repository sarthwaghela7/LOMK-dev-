"""Case lifecycle stages: the single authoritative classification of one call record.

Every call record (one call_analysis_results row) has its own stage. Nothing here groups, de-duplicates or
looks at other calls: client number, context, lead and agent never influence a call's stage.

Stages            Case Status Change values (exact dropdown wording, compared trimmed / case-insensitive)
open              Open, Blocker, Same Client Context, blank/NULL (and any unrecognised value)
                  NOTE: the Calls-page "Open Cases" button and the Dashboard Open card use the wider status list
                  (blank, Open, Blocker, Same Client Context, Client Side Delay, Dropped), see analysis_read_query.AGING_STATUSES.
in_transition     Initial email sent (Fee Details shared), Initial Email Follow Up, Fee details shared & FU, PC link shared, PC Follow Up
dropped           Client Side Delay, Dropped, Wrong Number Dialed
success           Success PC, Success GE
lost              set ONLY by the scheduled processor (services/case_lost_processor.py): an In-Transition call that
                  stayed In-Transition for the Lost period. Its status becomes "<last In-Transition status> & Lost".

The same table is implemented in SQL (function fd_case_lifecycle_stage_of, migration 073) because a trigger must
classify every writer of fd_m_case_status_change (the Calls page, the related-call closure confirmation, imports).
Tests/test_case_lifecycle.py asserts that the two implementations agree on every value.

Precedence (first match wins): Lost suffix -> Success -> Dropped -> In-Transition -> Open.
A blank/NULL status never erases an established Lost / Dropped / Success outcome (database trigger rule).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

OPEN = "open"
IN_TRANSITION = "in_transition"
LOST = "lost"
DROPPED = "dropped"
SUCCESS = "success"

# Order of the five Calls-page buttons / Dashboard cards.
STAGES = (OPEN, IN_TRANSITION, LOST, DROPPED, SUCCESS)
STAGE_LABELS = {
    OPEN: "Open Cases", IN_TRANSITION: "In-Transition Cases", LOST: "Lost Cases",
    DROPPED: "Dropped Cases", SUCCESS: "Success Cases",
}

LOST_SUFFIX = " & Lost"

# The Case Status Change dropdown, in display order. Lifecycle stages are NOT dropdown values.
CASE_STATUS_OPTIONS = (
    "Open",
    "Client Side Delay",
    "Blocker",
    "Dropped",
    "Same Client Context",
    "Wrong Number Dialed",
    "Initial email sent (Fee Details shared)",
    "Initial Email Follow Up",
    "Fee details shared & FU",
    "PC link shared",
    "PC Follow Up",
    "Success PC",
    "Success GE",
)

_OPEN_STATUSES = ("Open", "Blocker", "Same Client Context")
IN_TRANSITION_STATUSES = ("Initial email sent (Fee Details shared)", "Initial Email Follow Up", "Fee details shared & FU", "PC link shared", "PC Follow Up")
_DROPPED_STATUSES = ("Client Side Delay", "Dropped", "Wrong Number Dialed")
_SUCCESS_STATUSES = ("Success PC", "Success GE")

# Statuses that close a case for the existing related-call closure feature and the open-call aging.
CLOSURE_STATUS_OPTIONS = ("Dropped", "Success GE", "Success PC")


def normalize(value) -> str:
    """Comparison key: trimmed, single-spaced, lower-case. NULL -> ''."""
    return " ".join(str(value or "").split()).lower()


# Spellings stored before the dropdown change -> the exact new value. A stale browser tab that still sends one of
# these is accepted and stored under the new wording. PROPOSED mapping for the ambiguous ones is marked.
LEGACY_STATUS_ALIASES = {
    "success - pc": "Success PC",
    "success - ge": "Success GE",
    "client side delay": "Client Side Delay",
    "same client context": "Same Client Context",
    "initial email followup": "Initial Email Follow Up",
    "initial email sent.": "Initial email sent (Fee Details shared)",
    "pc shared and lost": "PC link shared & Lost",              # PROPOSED (an existing Lost outcome)
}
# "Move to next date" has no counterpart in the new list: undecided. Classified as Open meanwhile and reported by the dry run.
UNMAPPED_LEGACY_STATUSES = ("move to next date",)

_BY_KEY = {normalize(v): v for v in CASE_STATUS_OPTIONS}


def canonical_status(value) -> str | None:
    """The exact dropdown wording for a stored/submitted value, or None when it is not (or no longer) a dropdown value.

    Legacy spellings map to their new wording; a "<status> & Lost" value is returned in canonical form too.
    """
    key = normalize(value)
    if not key:
        return None
    if key in _BY_KEY:
        return _BY_KEY[key]
    if key in LEGACY_STATUS_ALIASES:
        return LEGACY_STATUS_ALIASES[key]
    if key.endswith(normalize(LOST_SUFFIX)):
        base = canonical_status(key[: -len(normalize(LOST_SUFFIX))])
        if base in IN_TRANSITION_STATUSES:
            return base + LOST_SUFFIX
    return None


def accepted_status(value, current=None) -> str | None:
    """The value to STORE for a submitted Case Status Change, or None when it is not allowed.

    The 11 dropdown values are accepted (case/spacing-insensitive); a stale browser tab's legacy spelling is accepted and
    stored under the new wording; a "... & Lost" value is accepted only unchanged (re-saving a Lost call), never typed in.
    """
    key = normalize(value)
    if key in _BY_KEY:
        return _BY_KEY[key]
    if key in LEGACY_STATUS_ALIASES and not is_lost_status(key):
        return LEGACY_STATUS_ALIASES[key]
    if key and key == normalize(current) and is_lost_status(current):
        return str(current).strip()
    return None


def spellings_of(canonical: str) -> tuple[str, ...]:
    """Every normalised spelling that means `canonical` (the new wording plus legacy ones), for SQL IN (...) matching."""
    return tuple(sorted({normalize(canonical)} | {k for k, v in LEGACY_STATUS_ALIASES.items() if v == canonical}))


def is_lost_status(value) -> bool:
    return normalize(value).endswith("& lost") or normalize(value) == "pc shared and lost"


def lifecycle_stage_of(status) -> str:
    """Stage implied by a Case Status Change value alone (blank -> open). The database trigger adds history rules."""
    if is_lost_status(status):
        return LOST
    canonical = canonical_status(status)
    if canonical in _SUCCESS_STATUSES:
        return SUCCESS
    if canonical in _DROPPED_STATUSES:
        return DROPPED
    if canonical in IN_TRANSITION_STATUSES:
        return IN_TRANSITION
    return OPEN


def lost_status_for(last_in_transition_status: str) -> str:
    """Last valid In-Transition status + ' & Lost', appended exactly once."""
    base = str(last_in_transition_status or "").strip()
    if normalize(base).endswith(normalize(LOST_SUFFIX)):
        return base
    return base + LOST_SUFFIX


# ------------------------------------------------------------------ the 30-day rule
CENTRAL = ZoneInfo("America/Chicago")      # the business timezone used by the Dashboard and the scheduler
# An In-Transition call becomes eligible for Lost when (today - entry date) in calendar days reaches this value, both
# dates in America/Chicago. Required example: entered Oct 20 -> still In-Transition through Nov 19 (30 days),
# Nov 20 is the boundary day (31 days, not yet Lost), Nov 21 (32 days) is Lost when the processor runs that day.
LOST_AFTER_DAYS = 32


def central_date(value: datetime | date) -> date:
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=ZoneInfo("UTC"))
        return value.astimezone(CENTRAL).date()
    return value


def central_today() -> date:
    return datetime.now(CENTRAL).date()


def is_lost_eligible(entered_at: datetime | date | None, today: date | None = None) -> bool:
    if entered_at is None:
        return False
    return (today or central_today()) - central_date(entered_at) >= timedelta(days=LOST_AFTER_DAYS)


def entered_on_or_before(today: date | None = None) -> date:
    """Latest In-Transition entry date (Central) that is eligible for Lost today."""
    return (today or central_today()) - timedelta(days=LOST_AFTER_DAYS)


# ------------------------------------------------------------------ Dashboard KPI counts
def lifecycle_counts(db, *, start, end, agent="all", number="", extension="") -> dict:
    """The five Dashboard lifecycle KPIs for the Calls-page population under the same Date / Agent / Lead Number / Extension filters.

    One count per call record: open + in_transition + lost + dropped + success == total. Aggregated in SQL by
    services.analysis_read_query.analysis_read(lifecycle_counts=True).
    """
    from services.analysis_read_query import analysis_read
    found = analysis_read(
        db, start=start, end=end, call_source=True, target_page="calls", lifecycle_counts=True,
        number=number or None, extension=extension or None, agent=None if agent in (None, "", "all") else agent,
    )
    counts = {stage: int(found.get(stage, 0)) for stage in STAGES}
    # Open Cases is the status-based list (it includes Client Side Delay and Dropped calls, which are also in Dropped Cases),
    # so the cards overlap and `total` is the number of distinct calls, not their sum.
    return {"counts": counts, "total": int(found.get("_total", sum(counts.values())))}


# ------------------------------------------------------------------ Dashboard calculator input
# The Dashboard calculator (services/dashboard_calculator.mjs, bundled from the frontend) and its Call Log Status
# columns are keyed by the spellings used before the dropdown change. Its formulas are deliberately untouched: rows are
# handed to it with the status in that spelling, so every existing metric keeps meaning what it meant.
_METRIC_SPELLING = {
    "success pc": "Success - PC", "success ge": "Success - GE",
    "initial email follow up": "initial email followup",
    "initial email sent (fee details shared)": "Initial email sent.",
    "client side delay": "Client Side delay", "same client context": "Same client context",
}


def metric_status(value):
    """Status in the spelling the Dashboard calculator's formulas expect. (Its Call Log Status columns are the dropdown values
    and read these spellings back through an alias table; a Lost status has no column and is reported as "not in the list".)"""
    return _METRIC_SPELLING.get(normalize(value), value)


def for_metric_calculator(rows):
    """Rows with fd_m_case_status_change rewritten by metric_status (new dicts; the input is not modified)."""
    return [{**row, "fd_m_case_status_change": metric_status(row.get("fd_m_case_status_change"))}
            if row.get("fd_m_case_status_change") else row for row in rows]
