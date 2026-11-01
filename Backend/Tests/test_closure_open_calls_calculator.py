"""An inherited closure (another agent's confirmed closure of the same matter) must not count as open.

Runs the real versioned Dashboard calculator bundle (Node), exactly as services.dashboard_summary does.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

BUNDLE = Path(__file__).resolve().parents[1] / "services" / "dashboard_calculator.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the Dashboard calculator")

INHERITED = {"closure_id": "c1", "closing_agent_name": "Priya Sharma", "closure_status": "Success - GE"}


def _row(i, **extra):
    return {"id": i, "call_id": f"00000000-0000-0000-0000-00000000000{i}", "call_recording_date_normalized": "2026-10-01",
            "call_recording_date": "2026-10-01", "front_desk_person_name": "Ravi", "fd_m_case_status_change": None,
            "fd_a_case_type": "H-1B", "src": "3125550100", "dst": "2243860601", "billsec": 60, **extra}


def _run(rows):
    payload = {"dashboardAnalysis": rows, "dashboardCalls": [], "analysis": rows, "calls": [], "tasks": [], "leads": [],
               "dateFrom": "2026-10-01", "dateTo": "2026-10-31", "dailyTargetCalls": [], "fdAgents": [], "syncFilters": [],
               "dependencySummary": {}, "taskSummary": {}, "timeDurations": {}}
    done = subprocess.run(["node", str(BUNDLE)], input=json.dumps(payload), capture_output=True, text=True,
                          env={"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "TZ": "America/Chicago"})
    assert done.returncode == 0, done.stderr[-500:]
    return json.loads(done.stdout)


def _day(result, date="2026-10-01"):
    return next(row for row in result["callLogStatusRows"] if row["date"] == date)


def test_open_leads_drop_an_inherited_closure_but_keep_everything_else():
    baseline = _day(_run([_row(1), _row(2)]))
    with_closure = _day(_run([_row(1), _row(2, closure_inherited=INHERITED)]))
    assert baseline["openLeads"] == 2
    assert with_closure["openLeads"] == 1 and with_closure["total"] == 2   # still counted as a call, just not open


def test_an_inherited_closure_is_never_counted_as_a_success_or_drop():
    day = _day(_run([_row(1, fd_m_case_status_change="Open", closure_inherited=INHERITED)]))
    assert day["successGE"] == 0 and day["successPC"] == 0 and day["dropped"] == 0   # no closure credit
    assert day["open"] == 0                                                          # a confirmed closure is no longer open
    assert day["openLeads"] == 0


def test_an_independent_closure_still_closes_and_still_gets_credit():
    day = _day(_run([_row(1, fd_m_case_status_change="Success - GE")]))
    assert day["successGE"] == 1 and day["openLeads"] == 0


def test_open_call_aging_excludes_inherited_closures():
    old = [_row(1, call_recording_date_normalized="2000-01-01"), _row(2, call_recording_date_normalized="2000-01-01")]
    aging = _run(old)["openCallAges"]
    aging_closed = _run([old[0], {**old[1], "closure_inherited": INHERITED}])["openCallAges"]
    flat = lambda value: json.dumps(value, sort_keys=True)
    assert flat(aging) != flat(aging_closed)
    assert max(int(v) for v in json.loads(flat(aging_closed)).values() if isinstance(v, int)) < \
        max(int(v) for v in json.loads(flat(aging)).values() if isinstance(v, int))


# ---- Open Call Aging uses the Dashboard's own open rule and the Calls page's Open Cases rule -------------------------
from datetime import datetime, timedelta, timezone  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402


def _central_today():
    return datetime.now(ZoneInfo("America/Chicago")).date()


def _aged(i, age, status=None, **extra):
    day = (_central_today() - timedelta(days=age)).isoformat()
    return _row(i, call_recording_date_normalized=day, call_recording_date=day, fd_m_case_status_change=status, **extra)


def test_aging_cards_count_blank_and_the_five_open_list_statuses_only():
    rows = [_aged(1, 3, "Open"), _aged(2, 5, "PC Follow Up"), _aged(3, 10, "Blocker", case_closing_completed=True),
            _aged(4, 12, "Dropped"), _aged(5, 15, "Success - GE"), _aged(6, 20, "Success - PC"),
            _aged(7, 11, None, fd_m_manual_action_status="Completed"), _aged(8, 30, "Open", closure_inherited=INHERITED), _aged(9, 4),
            _aged(10, 25, "Wrong Number Dialed"), _aged(11, 25, "Client Side delay"), _aged(12, 25, "Same client context")]
    ages = _run(rows)["openCallAges"]
    # counted: 1 Open, 3 Blocker, 4 Dropped, 7 blank, 9 blank, 11 Client Side Delay, 12 Same Client Context (ages 3, 10, 12, 11, 4, 25, 25)
    # not counted: PC Follow Up, Success GE / PC, the inherited closure, Wrong Number Dialed (ages 5, 15, 20, 30, 25)
    assert set(ages) == {"3", "5", "10", "15"}                  # the card shape is unchanged
    assert (ages["3"], ages["5"], ages["10"], ages["15"]) == (7, 5, 5, 2)
    assert ages["3"] >= ages["5"] >= ages["10"] >= ages["15"]


def test_aging_counts_each_call_once_and_never_buckets_an_undated_call():
    twice = _aged(1, 12)
    broken = _row(2, call_recording_date_normalized=None, call_recording_date=None)
    out = _run([twice, {**twice}, broken])["openCallAges"]
    assert out == {"3": 1, "5": 1, "10": 1, "15": 0}            # the two copies of one call count once; the undated call is in no bucket
