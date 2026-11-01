"""The deployed Node calculator must count open cases with the same rule as the frontend source:
open = every Case Status Change except Success - PC / Success - GE / Dropped (blank = open)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

BUNDLE = Path(__file__).resolve().parents[1] / "services" / "dashboard_calculator.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")


def _row(i, status, agent="Ravi"):
    return {"id": i, "call_id": f"00000000-0000-0000-0000-0000000000{i:02d}", "call_recording_date_normalized": "2026-10-01",
            "call_recording_date": "2026-10-01", "front_desk_person_name": agent, "fd_m_case_status_change": status,
            "fd_a_case_type": "H-1B", "src": "3125550100", "dst": "2243860601", "billsec": 60}


def _run(rows):
    payload = {"dashboardAnalysis": rows, "dashboardCalls": [], "analysis": rows, "calls": [], "tasks": [], "leads": [],
               "dateFrom": "2026-10-01", "dateTo": "2026-10-01", "dailyTargetCalls": [], "fdAgents": [], "syncFilters": [],
               "dependencySummary": {}, "taskSummary": {}, "timeDurations": {}}
    done = subprocess.run(["node", str(BUNDLE)], input=json.dumps(payload), capture_output=True, text=True,
                          env={"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "TZ": "America/Chicago"})
    assert done.returncode == 0, done.stderr[-400:]
    return json.loads(done.stdout)


def test_open_counts_follow_the_business_rule():
    statuses = [None, "", "Open", "PC Follow Up", "Success - GE", "Dropped", "Success - PC", "Fee details shared & FU"]
    out = _run([_row(i + 1, s) for i, s in enumerate(statuses)])
    assert out["callLogStatusRows"][0]["open"] == 5
    assert out["callLogStatusRows"][0]["openLeads"] == 5     # Call Updates uses the same population
    assert out["performanceRows"][0]["openCases"] == 5       # a count, not average open days


def test_all_terminal_statuses_means_zero_open():
    out = _run([_row(1, "Success - GE"), _row(2, "dropped"), _row(3, " Success - PC ")])
    assert out["callLogStatusRows"][0]["open"] == 0
