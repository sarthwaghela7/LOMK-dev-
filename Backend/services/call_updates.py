"""Daily Call Updates summary: one number per (date, agent, metric).

Nothing here re-implements a business rule; each figure comes from the
existing source of truth for it:

* Incoming / Outgoing Calls -- the Calls page query (analysis_read with
  target_page="calls"), direction by the same rule as its Call Direction
  filter, and the agent shown in the Calls page Agent column.
* Missed Calls -- the Missed Calls page tasks; a missed call is credited to
  the agent who called back (its "Called Back" agent), on the callback day.
* Open Leads / GE / PC / Dropped / VM -- the Dashboard's Call Log Status,
  computed by the Dashboard's own calculator once per agent.
* Calls Answered / Calls Missed-WA / Initial Emails Sent -- manual entries.
"""
from __future__ import annotations

import json
import os
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from fastapi.encoders import jsonable_encoder

from services.dashboard_summary import invoke_read

CASE_STATUS_FIELDS = {"openLeads": "open_leads", "successGE": "successful_ge", "successPC": "paid_pc",
                      "dropped": "dropped", "vm": "vm"}
MANUAL_METRICS = ("wa_answered", "wa_missed", "initial_emails")

# Agents are shown in the order the business reads them (first name, case-insensitive); anyone else follows in the existing order.
AGENT_DISPLAY_ORDER = ("shrishti", "priyanka", "gokul", "gracy", "christobel", "edgin")


def order_agents(agents: list[dict]) -> list[dict]:
    def rank(agent):
        first = str(agent.get("name") or "").strip().split(" ")[0].lower()
        return AGENT_DISPLAY_ORDER.index(first) if first in AGENT_DISPLAY_ORDER else len(AGENT_DISPLAY_ORDER)
    return sorted(agents, key=rank)          # stable: ties keep their existing order


def retainer_counts(db, start: date, end: date) -> dict[tuple[str, str], dict[str, int]]:
    """{(iso call date, owner agent id): {...}} from the Follow Ups tab (fd_follow_up_retainer): submitted calls per Retainer Owner,
    by the call's date. retainer_follow_up = Follow Up Required "Yes"; retainer_lost / retainer_success = Retainer Status."""
    from sqlalchemy.exc import ProgrammingError

    try:
        rows = _retainer_rows(db, start, end)
    except ProgrammingError:             # migration 077 not applied yet: the Follow Ups rows are simply zero
        db.rollback()
        return {}
    return {(day.isoformat(), owner): {"retainer_follow_up": int(f), "retainer_lost": int(l), "retainer_success": int(s)} for day, owner, f, l, s in rows}


def _retainer_rows(db, start: date, end: date):
    from sqlalchemy import text

    rows = db.execute(text("""
        SELECT c.call_date, r.retainer_owner_id::text,
               count(*) FILTER (WHERE r.retainer_follow_up_required = 'Yes'),
               count(*) FILTER (WHERE r.retainer_status = 'Lost'),
               count(*) FILTER (WHERE r.retainer_status = 'Success')
        FROM fd_follow_up_retainer r JOIN calls c ON c.call_id = r.call_id
        WHERE r.submitted_at IS NOT NULL AND r.retainer_owner_id IS NOT NULL AND c.call_date BETWEEN :start AND :end
        GROUP BY c.call_date, r.retainer_owner_id
    """), {"start": start, "end": end}).all()
    return rows


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def call_direction_counts(db, start: date, end: date) -> dict[tuple[str, str], dict[str, int]]:
    """{(iso date, agent name): {"incoming": n, "outgoing": n}} for the Calls page."""
    from routes.calls import (
        _agent_names_with_manual_override, _normalize_routing_number, _system_numbers,
    )
    from services.analysis_read_query import analysis_read

    calls, _, _ = analysis_read(db, start=start, end=end, call_source=True, target_page="calls")
    system = _system_numbers(db)
    names = _agent_names_with_manual_override(db, calls)
    counts: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"incoming": 0, "outgoing": 0})
    for call in calls:
        # Same split as the Calls page Call Direction filter.
        outbound = _normalize_routing_number(call.src) in system
        inbound = not outbound and _normalize_routing_number(call.dst) in system
        if not (outbound or inbound) or not call.call_date:
            continue
        agent = names.get(str(call.call_id), "-")
        counts[(call.call_date.isoformat(), agent)]["outgoing" if outbound else "incoming"] += 1
    return counts


def fetch_tasks(db, user, start: date, end: date) -> list[dict]:
    """Missed Call and Non-Responsive tasks, exactly as the Dashboard loads them."""
    from routes.flintdesk.tasks import get as get_tasks

    return [row for kind in ("missed_call", "non_responsiveness")
            for row in invoke_read(get_tasks, db, user, start_date=start, end_date=end,
                                   task_type=kind, include_callback=True)]


def missed_call_counts(tasks: list[dict]) -> dict[tuple[str, str], int]:
    """{(iso date, called-back agent): n} from the Missed Calls page tasks."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for task in tasks:
        if task.get("task_type") != "missed_call":
            continue
        callback = (task.get("data") or {}).get("callback")
        agent = (callback or {}).get("agent")
        called_back_on = str((callback or {}).get("called_back_date") or "")[:10]
        # No callback, or no resolvable agent: not credited to anyone.
        if not callback or not agent or agent == "-" or not called_back_on:
            continue
        counts[(called_back_on, agent)] += 1
    return counts


def _run_calculator(payload: dict) -> dict:
    process = subprocess.run(
        [os.getenv("DASHBOARD_NODE_BINARY", "node"), "--max-old-space-size=256",
         str(Path(__file__).with_name("dashboard_calculator.mjs"))],
        input=json.dumps(jsonable_encoder(payload)), capture_output=True, text=True, timeout=90,
        env={"PATH": os.environ.get("PATH", ""), "TZ": "America/Chicago"},
    )
    if process.returncode:
        raise RuntimeError("Dashboard calculator failed; verify the versioned bundle and Node runtime")
    return json.loads(process.stdout)


def _strip_heavy(value):
    if isinstance(value, dict):
        return {k: _strip_heavy(v) for k, v in value.items() if k not in {"transcript", "raw_llm_json", "html_summary"}}
    if isinstance(value, list):
        return [_strip_heavy(v) for v in value]
    return value


def dashboard_inputs(db, user, start: date, end: date, agents: list[dict]) -> dict:
    """The same call lists the Dashboard summary feeds its calculator."""
    from routes.calls import get_calls, serialize_analysis_rows
    from routes.flintdesk.sync_filters import list_sync_filters
    from services.analysis_read_query import analysis_read

    def analysis(dashboard):
        rows, calls_by_id, _ = analysis_read(db, start=start, end=end, dashboard=dashboard)
        from services.case_lifecycle import for_metric_calculator
        return for_metric_calculator(serialize_analysis_rows(rows, db, include_raw=False, calls_by_id=calls_by_id, include_transcript=False))

    dates = {"start_date": start, "end_date": end, "number": None, "extension": None}
    dashboard_analysis, full_analysis = analysis(True), analysis(False)
    # Open Leads here means Case Status Change is anything except Success - PC,
    # Success - GE or Dropped (blank included), read from the calculator's
    # ``openLeads``. Drop the Dashboard's Action Status fallback so only the
    # Case Status Change field decides the other counts.
    for row in (*dashboard_analysis, *full_analysis):
        if not str(row.get("fd_m_case_status_change") or "").strip() and \
                str(row.get("action_status") or "").strip().lower() == "open":
            row["action_status"] = None
    return {
        "dateFrom": str(start), "dateTo": str(end), "selectedNumber": "", "selectedLeadNumber": "",
        "fdAgents": agents, "syncFilters": invoke_read(list_sync_filters, db, user),
        "dashboardAnalysis": dashboard_analysis,
        "dashboardCalls": invoke_read(get_calls, db, user, **dates, target_page="dashboard"),
        "analysis": full_analysis,
        "calls": invoke_read(get_calls, db, user, **dates, target_page="calls"),
        # Leads only add badges to call rows; they never change a count.
        "leads": [],
    }


def case_status_counts(inputs: dict, tasks: list[dict], agents: list[dict]) -> dict[tuple[str, str], dict[str, int]]:
    """{(iso date, agent name): {open_leads, successful_ge, ...}} from the
    Dashboard's Call Log Status, computed by the Dashboard's own calculator
    once per agent."""
    base = _strip_heavy({**inputs, "tasks": tasks})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda agent: _run_calculator({**base, "selectedAgent": agent["name"]}), agents))
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for agent, result in zip(agents, results):
        for row in result.get("callLogStatusRows", []):
            counts[(row["date"], agent["name"])] = {
                **{out: int(row.get(src) or 0) for src, out in CASE_STATUS_FIELDS.items()},
                # Per Case Status Change value (the calculator's own Call Log Status columns) and per "... & Lost" value.
                "status_counts": {label: int(count or 0) for label, count in (row.get("statusCounts") or {}).items()},
                "lost_counts": {label: int(count or 0) for label, count in (row.get("lostCounts") or {}).items()},
            }
    return counts


def manual_entries(db, start: date, end: date) -> dict[tuple[str, str], dict]:
    from storage.models import FDCallUpdateEntry

    entries: dict[tuple[str, str], dict] = {}
    for row in db.query(FDCallUpdateEntry).filter(FDCallUpdateEntry.entry_date.between(start, end)).all():
        entries.setdefault((row.entry_date.isoformat(), str(row.agent_id)), {})[row.metric] = {
            "value": row.value, "note": row.note or "",
        }
    return entries


def _in_session(function, *args):
    """Run a read in its own DB session so independent reads can overlap."""
    from storage.db import SessionLocal

    session = SessionLocal()
    try:
        return function(session, *args)
    finally:
        session.close()


def build_call_updates(db, user, start: date, end: date, agents: list[dict]) -> dict:
    """Assemble {agents, days:[{date, agents:{agent_id:{...}}}]} for [start, end]."""
    agents = order_agents(agents)
    with ThreadPoolExecutor(max_workers=3) as pool:
        directions_job = pool.submit(_in_session, call_direction_counts, start, end)
        tasks_job = pool.submit(_in_session, lambda session: fetch_tasks(session, user, start, end))
        inputs_job = pool.submit(_in_session, lambda session: dashboard_inputs(session, user, start, end, agents))
        directions, tasks, inputs = directions_job.result(), tasks_job.result(), inputs_job.result()
    missed = missed_call_counts(tasks)
    case_status = case_status_counts(inputs, tasks, agents)
    manual = manual_entries(db, start, end)
    retainer = retainer_counts(db, start, end)
    days = []
    for day in _days(start, end):
        iso = day.isoformat()
        per_agent = {}
        for agent in agents:
            name, agent_id = agent["name"], agent["id"]
            calls = directions.get((iso, name), {})
            status = case_status.get((iso, name), {})
            entry = manual.get((iso, agent_id), {})
            per_agent[agent_id] = {
                "incoming": calls.get("incoming", 0),
                "outgoing": calls.get("outgoing", 0),
                "missed": missed.get((iso, name), 0),
                **{field: status.get(field, 0) for field in CASE_STATUS_FIELDS.values()},
                "status_counts": status.get("status_counts", {}),
                "lost_counts": status.get("lost_counts", {}),
                **{key: retainer.get((iso, agent_id), {}).get(key, 0) for key in ("retainer_follow_up", "retainer_lost", "retainer_success")},
                "wa_answered": entry.get("wa_answered", {}).get("value", 0),
                "wa_missed": entry.get("wa_missed", {}).get("value", 0),
                "initial_emails": entry.get("initial_emails", {}).get("value", 0),
                "initial_emails_note": entry.get("initial_emails", {}).get("note", ""),
            }
        days.append({"date": iso, "agents": per_agent})
    return {"agents": [{"id": a["id"], "name": a["name"]} for a in agents], "days": days}
