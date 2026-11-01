"""One cached Dashboard read; the versioned calculator preserves UI rules."""
import inspect
import json
import logging
import os
from pathlib import Path
import subprocess

from fastapi.encoders import jsonable_encoder
from fastapi.params import Param

logger = logging.getLogger("autoflint")

_CALCULATOR_TIMEOUT_SECONDS = 40


def invoke_read(function, db, user, **values):
    function = inspect.unwrap(function)
    arguments = {}
    for name, parameter in inspect.signature(function).parameters.items():
        if name in values:
            arguments[name] = values[name]
        elif name == "db":
            arguments[name] = db
        elif name in {"user", "_user"}:
            arguments[name] = user
        elif isinstance(parameter.default, Param):
            arguments[name] = parameter.default.default
        elif parameter.default is not inspect.Parameter.empty:
            arguments[name] = parameter.default
    return function(**arguments)


def build_summary(db, user, start, end, agent="all", number="", extension=""):
    from routes.calls import get_analysis, get_calls
    from routes.fd_users import list_front_desk_agents
    from routes.flintdesk.leads import list_leads_for_dashboard
    from routes.flintdesk.tasks import get as get_tasks, dashboard_summary as task_summary
    from routes.flintdesk.dependencies import dependency_dashboard_summary
    from routes.flintdesk.dashboard_metrics import get_time_duration
    from routes.flintdesk.sync_filters import list_sync_filters

    dates = {"start_date": start, "end_date": end}
    call_filters = {**dates, "number": number or None, "extension": extension or None}
    agents = invoke_read(list_front_desk_agents, db, user)
    def summary_analysis(dashboard=False):
        # Private calculator input retains documentation fields for exact KPI
        # parity; public table routes never return these long-text columns.
        from services.analysis_read_query import analysis_read
        from routes.calls import serialize_analysis_rows
        rows, calls, _ = analysis_read(db, start=start, end=end, dashboard=dashboard,
                                      number=number or None, extension=extension or None)
        from services.case_lifecycle import for_metric_calculator      # the calculator's formulas are keyed by the older spellings
        return for_metric_calculator(serialize_analysis_rows(rows, db, include_raw=False,
                                                             calls_by_id=calls, include_transcript=False))
    payload = {
        "dateFrom": str(start), "dateTo": str(end), "selectedAgent": agent,
        "selectedNumber": number, "selectedLeadNumber": extension,
        "fdAgents": agents, "syncFilters": invoke_read(list_sync_filters, db, user),
        "dashboardAnalysis": summary_analysis(dashboard=True),
        "dashboardCalls": invoke_read(get_calls, db, user, **call_filters, target_page="dashboard"),
        "analysis": summary_analysis(),
        "calls": invoke_read(get_calls, db, user, **call_filters, target_page="calls"),
        "leads": list_leads_for_dashboard(db, start, end),
        # General tasks are private and never become call records. Only these
        # two globally readable task types may enter a shared summary cache.
        "tasks": [row for kind in ("missed_call", "non_responsiveness")
                  for row in invoke_read(get_tasks, db, user, **dates, task_type=kind, include_callback=True)],
        "dependencySummary": invoke_read(dependency_dashboard_summary, db, user, **dates),
        "taskSummary": invoke_read(task_summary, db, user, **dates, agent="" if agent == "all" else agent),
        "timeDurations": invoke_read(get_time_duration, db, user, **dates,
            agent_ids=[row["id"] for row in agents if row["name"] == agent] if agent != "all" else None),
    }
    from services.dashboard_ops_metrics import build_ops_metrics
    ops = build_ops_metrics(
        db, start=start, end=end, agent=agent, number=number, extension=extension,
        leads=payload["leads"], tasks=payload["tasks"],
        dashboard_analysis=payload["dashboardAnalysis"], dashboard_calls=payload["dashboardCalls"],
    )
    result = {**_run_calculator(db, payload), **ops}
    return result


def open_call_aging_payload(db, *, start, end, agent="all", number="", extension=""):
    """Open Call Aging for the four Dashboard cards, served by its OWN endpoint (/api/dashboard/open-call-aging).

    It reads the Calls page's own "Open Cases" population (services/open_call_aging.py). It is deliberately NOT part of
    build_summary: if these figures are slow, the rest of the Dashboard must still arrive at once, and the page fills the
    four cards in when this answer lands.
    """
    from services.open_call_aging import aging_bundle
    from utils.config import OPEN_CALL_DEPENDENCY_AI_ENABLED
    found = aging_bundle(db, start=start, end=end, agent=agent, number=number, extension=extension)
    context = {"sameClientContext": found["sameClientContext"]}
    if agent not in (None, "", "all"):
        # Shown on the four cards only while one agent is selected (saved AI answers: services/dependency_ai.py).
        context.update({key: found[key] for key in ("agentDependency", "otherAgentDependency", "unanalysed")})
        context["agent"] = agent
        context["aiEnabled"] = bool(OPEN_CALL_DEPENDENCY_AI_ENABLED)
        from services.dependency_ai import ai_start_date
        context["aiSince"] = ai_start_date().isoformat()        # the AI only reads calls from this date onward
    return {"openCallAges": found["ages"], "openCallAgeContext": context}


def _strip_heavy(value):
    if isinstance(value, dict):
        return {key: _strip_heavy(item) for key, item in value.items()
                if key not in {"transcript", "raw_llm_json", "html_summary"}}
    if isinstance(value, list):
        return [_strip_heavy(item) for item in value]
    return value


_ROW_LISTS = ("dashboardAnalysis", "analysis", "dashboardCalls", "calls")


def _share_rows(payload):
    """Send each distinct analysis/call row once.

    The Dashboard lists are overlapping reads of the same rows (dashboardAnalysis is a subset of analysis, dashboardCalls
    of calls), so a wide range used to ship every row twice and reached the calculator's input cap. The calculator
    (services/dashboard_calculator.mjs) rebuilds the four lists from `rowPool`; the figures are unchanged.
    """
    pool, seen = [], {}
    shared = dict(payload)
    for key in _ROW_LISTS:
        indexes = []
        for row in payload[key]:
            fingerprint = json.dumps(row, sort_keys=True)
            if fingerprint not in seen:
                seen[fingerprint] = len(pool)
                pool.append(row)
            indexes.append(seen[fingerprint])
        shared[key] = indexes
    shared["rowPool"] = pool
    return shared


def _run_calculator(db, payload):
    # No transcript/opaque provider JSON is needed by any KPI calculation.
    encoded = json.dumps(_share_rows(jsonable_encoder(_strip_heavy(payload))))
    db.rollback()  # Release the DB transaction before running pure calculations.
    process = subprocess.run(
        [os.getenv("DASHBOARD_NODE_BINARY", "node"), f"--max-old-space-size={os.getenv('DASHBOARD_NODE_HEAP_MB', '256')}",
         str(Path(__file__).with_name("dashboard_calculator.mjs"))],
        input=encoded, capture_output=True, text=True, timeout=_CALCULATOR_TIMEOUT_SECONDS,
        # The pure calculator needs no credentials; do not hand it the API's secrets.
        env={"PATH": os.environ.get("PATH", ""), "TZ": "America/Chicago"},
    )
    if process.returncode:
        logger.error("[dashboard] calculator rc=%s payload_bytes=%s stderr=%s",
                     process.returncode, len(encoded), (process.stderr or "")[-2000:])
        raise RuntimeError("Dashboard calculator failed; verify the versioned bundle and Node runtime")
    return json.loads(process.stdout)


def build_case_types_eq(db, user, start, end):
    """Case Types + EQ table rows for a date range.

    Built from the same canonical records, eligibility, Central-time dates and case-type rules as the Trends report
    (services/case_type_trends.py), so the table, the Trends figures, the question popup and the PDF always agree.
    Case types are whatever the AI analysis produced in the range; nothing is a fixed list.
    """
    from datetime import datetime
    from services.analysis_read_query import analysis_read
    from services.case_type_taxonomy import clean_question, is_enquiry_client_type
    from services.case_type_trends import CT, build_questions_table, enquiry_rows, fetch_call_rows

    rows = fetch_call_rows(db, start, end)
    table = build_questions_table(rows, start, end)
    # How many call records the Calls page lists for this range (no filters), counted exactly as that page does:
    # analysed records plus calls whose AI analysis has not arrived yet. Stamped with the time they were read.
    analysed = analysis_read(db, start=start, end=end, target_page="calls", page=1, page_size=1)[2]
    waiting = len(analysis_read(db, start=start, end=end, call_source=True, target_page="calls",
                                only_unanalyzed=True, ids_only=True))
    # Every record in range is accounted for exactly once: an enquiry question (the table below), a voicemail / spam
    # call, an enquiry-type call with no question recorded, another record the Dashboard rules do not count, or a call
    # still waiting for its AI analysis.
    enquiries = len(enquiry_rows(rows))
    voicemail_spam = sum(1 for r in rows if not is_enquiry_client_type(r.get("client_type")))
    no_question = sum(1 for r in rows if is_enquiry_client_type(r.get("client_type")) and not clean_question(r.get("question")))
    other = max(0, analysed - len(rows))
    return {"mode": "case_types_eq", "dateFrom": str(start), "dateTo": str(end), "caseTypeEnquiryRows": table,
            "summary": {"asOf": datetime.now(CT).isoformat(timespec="seconds"), "timezone": "America/Chicago",
                        "callRecords": analysed + waiting, "analysedRecords": analysed, "waitingForAnalysis": waiting,
                        "enquiryQuestions": enquiries, "voicemailOrSpam": voicemail_spam, "noQuestionRecorded": no_question,
                        "otherRecords": other}}
