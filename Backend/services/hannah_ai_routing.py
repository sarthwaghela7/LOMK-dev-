"""Shared Hannah AI ring-route correlation.

Two independent ring routes hand off to the Hannah AI attendant
(2243860601) when nobody picks up:

  - Front Desk Agent: lead 3128781255 -> ring route 601 -> on no-answer
    -> ring route 602 -> Hannah AI.
  - Website: lead 2243374447 -> ring route 603 -> on no-answer -> ring
    route 604 -> Hannah AI.

A single physical call can therefore be logged as multiple CDR rows: the
ring-group leg (dst=601 or 603, disposition "not answered"), the hand-off
leg (dst=602 or 604), and the Hannah AI leg itself (dst=2243860601).
Categorizing a call as Front Desk Agent or Website requires BOTH the
hand-off leg (602/604) AND the Hannah AI leg (2243860601) to be present
for the same call session --
either one alone is not enough to classify it (mirrors the existing
dst-only rule in services/missed_call_sync.is_missed_call and
routes/calls.py's _is_missed_call_number: a lone 2243860601 or a lone
602/604 elsewhere does not by itself mean a categorized Hannah AI call
happened).

Used by:
  - services/missed_call_sync.py, to label each auto-created Missed Call
    task with its category.
  - routes/calls.py, to keep the 601/603 ring-group leg of a categorized call
    off the main Calls page (it is an internal routing leg of a call
    already represented on the Missed Calls page, not a call of its own).

The correlation is entirely in-memory over the call rows already loaded by
its callers; enabling Website categorization therefore does not add any
database query or network request.
"""

from __future__ import annotations

HANNAH_AI_NUMBER = "2243860601"

FRONT_DESK_ROUTING_DST = "601"
FRONT_DESK_HANNAH_DST = "602"
WEBSITE_ROUTING_DST = "603"
WEBSITE_HANNAH_DST = "604"

# Each lead number's inbound ring route (see migrations 029 and 060): calls to the lead arrive at the agent's
# extension with dst = the route, not the lead number. Used ONLY to include those calls when someone filters
# by lead number; agent attribution has its own (assignment) rule and does not use this.
LEAD_RING_ROUTES = {
    "3128781255": FRONT_DESK_ROUTING_DST,   # 601
    "2243374447": WEBSITE_ROUTING_DST,      # 603
    "2243939098": "605",
}

CATEGORY_FRONT_DESK_AGENT = "front_desk_agent"
CATEGORY_WEBSITE = "website"

# The ring-group "not answered" legs that must not surface as their own
# Calls-page row once a qualifying Hannah AI hand-off exists for the same
# call session.
ROUTING_LEG_DSTS = frozenset({FRONT_DESK_ROUTING_DST, WEBSITE_ROUTING_DST})

# Every dst value this module's correlation logic cares about -- used to
# cheaply narrow a large calls list down to the handful of rows worth
# examining, before doing the (call_date, src) grouping below.
RELEVANT_HANNAH_DSTS = ROUTING_LEG_DSTS | {
    FRONT_DESK_HANNAH_DST,
    WEBSITE_HANNAH_DST,
    HANNAH_AI_NUMBER,
}

# Two hand-off CDR rows (602/604/2243860601 only) within this many seconds of
# each other, on the same call_date + src, are treated as legs of the SAME
# hand-off session -- mirrors
# services/missed_call_sync._PHYSICAL_LEG_TOLERANCE_SECONDS exactly (the
# two near-simultaneous CDR legs of one Hannah AI hand-off).
HANDOFF_TOLERANCE_SECONDS = 5

# How far (in seconds) a 601/603 ring-group leg may sit from its nearest
# matching hand-off session and still be considered part of the same call.
# 120s mirrors services/Database_sync/f1.py's own LEG_MERGE_TOLERANCE_SECONDS
# -- a ring-group timeout before the call hands off to the next route can
# leave a noticeable gap, unlike the near-simultaneous hand-off pair itself
# (HANDOFF_TOLERANCE_SECONDS above).
SESSION_TOLERANCE_SECONDS = 120

# Which routing leg is allowed to close out which session category.
_ROUTING_LEG_CATEGORY = {
    FRONT_DESK_ROUTING_DST: CATEGORY_FRONT_DESK_AGENT,
    WEBSITE_ROUTING_DST: CATEGORY_WEBSITE,
}


def normalize_routing_number(value: object) -> str:
    """Mirrors routes/calls.py's and services/missed_call_sync.py's own
    identical normalizers -- kept as its own copy rather than importing
    either, consistent with how this codebase already duplicates this
    exact two-line normalizer to avoid cross-module coupling."""
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def category_for_dst_set(dst_values: set[str]) -> str | None:
    """Classify a hand-off session from the set of normalized dst values
    seen across its CDR legs. Returns None when the session doesn't contain
    a qualifying hand-off + Hannah AI pair."""
    if HANNAH_AI_NUMBER not in dst_values:
        return None
    if FRONT_DESK_HANNAH_DST in dst_values:
        return CATEGORY_FRONT_DESK_AGENT
    if WEBSITE_HANNAH_DST in dst_values:
        return CATEGORY_WEBSITE
    return None


def _time_to_seconds(value) -> int:
    if not value:
        return 0
    return value.hour * 3600 + value.minute * 60 + value.second


def _handoff_sessions(calls: list) -> list[dict]:
    """Tightly cluster only the hand-off legs (602/604/2243860601) into
    sessions, each keyed by (call_date, normalized src) with a [start, end]
    second-of-day time range and a category. Deliberately excludes 601/603 --
    that's matched to a session afterwards by nearest proximity (see
    routing_leg_ids_to_exclude), not clustered transitively together with
    it, so two distinct hand-offs close in time never get merged.
    """
    handoff_dsts = {
        FRONT_DESK_HANNAH_DST,
        WEBSITE_HANNAH_DST,
        HANNAH_AI_NUMBER,
    }
    handoff_calls = [call for call in calls if normalize_routing_number(call.dst) in handoff_dsts]

    by_key: dict[tuple, list] = {}
    for call in handoff_calls:
        key = (call.call_date, normalize_routing_number(call.src))
        by_key.setdefault(key, []).append(call)

    sessions: list[dict] = []
    for (call_date, src), group in by_key.items():
        group.sort(key=lambda c: _time_to_seconds(c.call_time))
        cluster: list = []
        for call in group:
            if cluster and _time_to_seconds(call.call_time) - _time_to_seconds(cluster[-1].call_time) > HANDOFF_TOLERANCE_SECONDS:
                sessions.append(_session_from_cluster(call_date, src, cluster))
                cluster = []
            cluster.append(call)
        if cluster:
            sessions.append(_session_from_cluster(call_date, src, cluster))
    return sessions


def _session_from_cluster(call_date, src: str, cluster: list) -> dict:
    dst_values = {normalize_routing_number(call.dst) for call in cluster}
    times = [_time_to_seconds(call.call_time) for call in cluster]
    return {
        "call_date": call_date,
        "src": src,
        "start": min(times),
        "end": max(times),
        "category": category_for_dst_set(dst_values),
    }


def _distance_to_range(value: int, start: int, end: int) -> int:
    if value < start:
        return start - value
    if value > end:
        return value - end
    return 0


def routing_leg_ids_to_exclude(calls: list, *, tolerance_seconds: int = SESSION_TOLERANCE_SECONDS) -> set:
    """The 601/603 ring-group "not answered" legs that belong to a qualifying
    Hannah AI hand-off session for the same call_date + src.

    Each 601/603 leg is matched to its nearest same-route-type hand-off session
    (built by _handoff_sessions, tight 5s tolerance) rather than being
    clustered transitively with every relevant dst for that src -- see this
    module's docstring for the real-data case that motivated this.
    """
    routing_calls = [call for call in calls if normalize_routing_number(call.dst) in ROUTING_LEG_DSTS]
    if not routing_calls:
        return set()

    sessions_by_key: dict[tuple, list[dict]] = {}
    for session in _handoff_sessions(calls):
        if session["category"] is None:
            continue
        key = (session["call_date"], session["src"])
        sessions_by_key.setdefault(key, []).append(session)
    if not sessions_by_key:
        return set()

    excluded = set()
    for call in routing_calls:
        expected_category = _ROUTING_LEG_CATEGORY.get(normalize_routing_number(call.dst))
        key = (call.call_date, normalize_routing_number(call.src))
        candidates = [s for s in sessions_by_key.get(key, []) if s["category"] == expected_category]
        if not candidates:
            continue
        leg_seconds = _time_to_seconds(call.call_time)
        if any(_distance_to_range(leg_seconds, s["start"], s["end"]) <= tolerance_seconds for s in candidates):
            excluded.add(call.call_id)
    return excluded
