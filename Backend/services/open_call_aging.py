"""Open Call Aging for the Dashboard cards.

Population: every call whose Case Status Change is blank (no status chosen, or no analysis yet) or exactly one of Open,
Client Side Delay, Blocker, Dropped, Same Client Context (analysis_read(aging_open=True) on the Calls page scope), minus
logs that confirmed another agent's closure. Each call is one canonical record, so it is counted once per card. Any other
status (In-Transition, Success, Wrong Number Dialed, Lost) is not counted. It is read with the same date range, Agent, Lead
Number and Extension filters as the Calls page. The "Open Cases" lifecycle button on the Calls page is a different list
(Open, Blocker, Same Client Context, blank) and is untouched.

Age = whole calendar days from the call's date to today in America/Chicago (the business date). The four cards are
cumulative thresholds (3+, 5+, 10+, 15+): every threshold is counted independently from the same open population, so
they overlap and are never added together.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
THRESHOLDS = (3, 5, 10, 15)
SAME_CLIENT_CONTEXT = "Same Client Context"


def central_today() -> date:
    return datetime.now(CENTRAL).date()


def _open_call_dates(db, *, start, end, agent, number, extension, case_status=None) -> list[date]:
    from services.analysis_read_query import analysis_read
    rows = analysis_read(
        db, start=start, end=end, call_source=True, target_page="calls", aging_open=True, ids_only=True,
        number=number or None, extension=extension or None,
        agent=None if agent in (None, "", "all") else agent, case_status=case_status,
    )
    return [day for _, day in rows if isinstance(day, date)]


def _cumulative(dates: list[date], today: date) -> dict[str, int]:
    ages = [max(0, (today - day).days) for day in dates]
    return {str(limit): sum(1 for age in ages if age >= limit) for limit in THRESHOLDS}


def open_call_ages(db, *, start, end, agent="all", number="", extension="", today=None) -> dict[str, int]:
    """{"3": n, "5": n, "10": n, "15": n}: open calls at least that many days old."""
    dates = _open_call_dates(db, start=start, end=end, agent=agent, number=number, extension=extension)
    return _cumulative(dates, today or central_today())


def open_call_total(db, *, start, end, agent="all", number="", extension="") -> int:
    """Distinct open calls under the same filters (NOT the sum of the cumulative cards)."""
    return len(_open_call_dates(db, start=start, end=end, agent=agent, number=number, extension=extension))


def same_client_context_ages(db, *, start, end, agent="all", number="", extension="", today=None) -> dict[str, int]:
    """Of the open calls above, how many are marked "Same client context" in Case Status Change, per threshold."""
    dates = _open_call_dates(db, start=start, end=end, agent=agent, number=number, extension=extension,
                             case_status=SAME_CLIENT_CONTEXT)
    return _cumulative(dates, today or central_today())


def _open_calls(db, *, start, end, agent, number, extension):
    """[(call_id, call_date)] of the open calls (same read as open_call_ages)."""
    from services.analysis_read_query import analysis_read
    rows = analysis_read(
        db, start=start, end=end, call_source=True, target_page="calls", aging_open=True, ids_only=True,
        number=number or None, extension=extension or None,
        agent=None if agent in (None, "", "all") else agent,
    )
    return [(str(call_id), day) for call_id, day in rows if isinstance(day, date)]


def dependency_ages(db, *, start, end, agent, number="", extension="", today=None) -> dict:
    """Per threshold: of the selected agent's open calls, how many the AI found depend on that agent, and how many on other agents.

    Reads the saved AI answers only (services/dependency_ai.py) - the model is never called here. An open call
    with no saved answer yet is counted in `unanalysed`, in neither dependency figure.
    """
    from sqlalchemy import text
    from services.dependency_ai import META_KEY
    calls = _open_calls(db, start=start, end=end, agent=agent, number=number, extension=extension)
    saved: dict[str, str] = {}
    ids = [call_id for call_id, _ in calls]
    for offset in range(0, len(ids), 5000):
        rows = db.execute(text(
            "SELECT call_id::text, fd_stage2_ai_meta -> CAST(:key AS TEXT) ->> 'depends_on' FROM call_analysis_results WHERE call_id::text = ANY(:ids)"),
            {"key": META_KEY, "ids": ids[offset:offset + 5000]}).all()
        saved.update({call_id: value for call_id, value in rows if value})
    today = today or central_today()
    from services.dependency_ai import ai_start_date
    ai_start = ai_start_date()
    by_value = {"agent": [], "other_agent": []}
    unanalysed = 0
    for call_id, day in calls:
        value = saved.get(call_id)
        if value in by_value:
            by_value[value].append(day)
        elif value is None and day >= ai_start and (today - day).days >= 3:
            unanalysed += 1                    # only calls from the AI start date on, open 3+ days, are ever "waiting"
    return {"agentDependency": _cumulative(by_value["agent"], today),
            "otherAgentDependency": _cumulative(by_value["other_agent"], today),
            "unanalysed": unanalysed}


def aging_bundle(db, *, start, end, agent="all", number="", extension="", today=None) -> dict:
    """Everything the four Open Call Aging cards need from ONE read of the open population.

    open_call_ages(), same_client_context_ages() and dependency_ages() each re-ran the heavy Calls-page query; this runs it
    once and reads the two small per-call facts (Case Status, saved AI answer) for just those calls in one more query.
    The figures are identical to the three functions above (tests compare them).
    """
    from sqlalchemy import text
    from services.dependency_ai import META_KEY
    today = today or central_today()
    calls = _open_calls(db, start=start, end=end, agent=agent, number=number, extension=extension)
    state: dict[str, tuple[str, str | None]] = {}
    ids = [call_id for call_id, _ in calls]
    for offset in range(0, len(ids), 5000):
        rows = db.execute(text(
            "SELECT call_id::text, lower(btrim(coalesce(fd_m_case_status_change, ''))), "
            "fd_stage2_ai_meta -> CAST(:key AS TEXT) ->> 'depends_on' "
            "FROM call_analysis_results WHERE call_id::text = ANY(:ids)"),
            {"key": META_KEY, "ids": ids[offset:offset + 5000]}).all()
        state.update({call_id: (status, depends) for call_id, status, depends in rows})
    result = {
        "ages": _cumulative([day for _, day in calls], today),
        "sameClientContext": _cumulative(
            [day for call_id, day in calls if state.get(call_id, ("", None))[0] == SAME_CLIENT_CONTEXT.lower()], today),
    }
    if agent not in (None, "", "all"):
        from services.dependency_ai import ai_start_date
        ai_start = ai_start_date()
        by_value = {"agent": [], "other_agent": []}
        unanalysed = 0
        for call_id, day in calls:
            depends = state.get(call_id, ("", None))[1]
            if depends in by_value:
                by_value[depends].append(day)
            elif depends is None and day >= ai_start and (today - day).days >= 3:
                unanalysed += 1                # only calls from the AI start date on, open 3+ days, are ever "waiting"
        result.update({"agentDependency": _cumulative(by_value["agent"], today),
                       "otherAgentDependency": _cumulative(by_value["other_agent"], today), "unanalysed": unanalysed})
    return result
