"""Dashboard Call Log Status: columns are the Case Status Change dropdown's options, filled from the persisted status.

Runs the real versioned Dashboard calculator bundle (Node), exactly as services.dashboard_summary does.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BUNDLE = Path(__file__).resolve().parents[1] / "services" / "dashboard_calculator.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the Dashboard calculator")

# The Case Status Change dropdown (FlintDesk src/data/statusOptions.js CASE_STATUS_OPTIONS; same order as the Calls page). The frontend has the same
# literal in its own test, so changing the dropdown forces both repos to be updated together.
DROPDOWN = ["Open", "Client Side Delay", "Blocker", "Dropped", "Same Client Context", "Wrong Number Dialed",
            "Initial email sent (Fee Details shared)",
            "Initial Email Follow Up", "Fee details shared & FU", "PC link shared", "PC Follow Up", "Success PC", "Success GE"]


def _row(i, status=None, agent="Ravi", date="2026-10-01", **extra):
    return {"id": i, "call_id": f"00000000-0000-0000-0000-{i:012d}", "call_recording_date_normalized": date,
            "call_recording_date": date, "front_desk_person_name": agent, "fd_m_case_status_change": status,
            "fd_a_case_type": "H-1B", "src": "3125550100", "dst": "2243860601", "billsec": 60, **extra}


def _run(rows, agent="all", date_from="2026-10-01", date_to="2026-10-31"):
    payload = {"dashboardAnalysis": rows, "dashboardCalls": [], "analysis": rows, "calls": [], "tasks": [], "leads": [],
               "dateFrom": date_from, "dateTo": date_to, "selectedAgent": agent, "selectedNumber": "", "selectedLeadNumber": "",
               "dailyTargetCalls": [], "fdAgents": [], "syncFilters": [], "dependencySummary": {}, "taskSummary": {},
               "timeDurations": {}}
    done = subprocess.run(["node", str(BUNDLE)], input=json.dumps(payload), capture_output=True, text=True,
                          env={"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "TZ": "America/Chicago"})
    assert done.returncode == 0, done.stderr[-500:]
    return json.loads(done.stdout)


def _day(result, date="2026-10-01"):
    return next(row for row in result["callLogStatusRows"] if row["date"] == date)


def test_the_bundle_uses_exactly_the_dropdown_options_in_the_dropdown_order():
    text = BUNDLE.read_text()
    block = re.search(r"const CASE_STATUS_OPTIONS = \[(.*?)\];", text, re.S).group(1)
    assert re.findall(r'"([^"]*)"', block) == DROPDOWN
    assert list(_day(_run([_row(1)]))["statusCounts"]) == DROPDOWN


def test_each_dropdown_status_is_counted_in_its_own_column_exactly_once():
    rows = [_row(i + 1, status) for i, status in enumerate(DROPDOWN)]
    day = _day(_run(rows))
    for status in DROPDOWN:
        assert day["statusCounts"][status] == 1, status                                # Open included
    assert day["total"] == len(rows)


def test_the_open_column_counts_only_calls_whose_status_is_set_to_open():
    day = _day(_run([_row(1, "Open"), _row(2, " open "), _row(3, None), _row(4, "Blocker"), _row(5, "Success - PC"), _row(6, "Dropped")]))
    assert day["statusCounts"]["Open"] == 2                                          # only the two set to Open (spacing/case ignored)
    assert day["statusCounts"]["Blocker"] == 1 and day["statusCounts"]["Success PC"] == 1 and day["statusCounts"]["Dropped"] == 1
    assert day["noStatus"] == 1                                                      # the blank one is in no column, only in the total
    assert day["total"] == 6


def test_the_dashboards_broader_open_field_is_unchanged_for_other_readers():
    day = _day(_run([_row(1, "Open"), _row(2, None), _row(3, "Blocker"), _row(4, "Success - PC"), _row(5, "Dropped")]))
    assert day["open"] == 3 and day["openLeads"] == 3                                # Open, blank and in-progress; not Success/Dropped


def test_a_status_is_never_taken_from_the_unrelated_action_status_field():
    day = _day(_run([_row(1, None, action_status="Success - PC"), _row(2, None, action_status="Dropped")]))
    assert day["statusCounts"]["Success PC"] == 0 and day["statusCounts"]["Dropped"] == 0 and day["statusCounts"]["Open"] == 0
    assert day["successPC"] == 0 and day["dropped"] == 0
    assert day["noStatus"] == 2 and day["open"] == 2


def test_spacing_and_letter_case_do_not_hide_a_real_status():
    day = _day(_run([_row(1, "  success - pc "), _row(2, "FEE DETAILS SHARED & FU")]))
    assert day["statusCounts"]["Success PC"] == 1 and day["statusCounts"]["Fee details shared & FU"] == 1   # legacy spelling lands in the new column


def test_unknown_and_missing_statuses_are_reported_not_forced_into_a_column():
    day = _day(_run([_row(1, "Some legacy value"), _row(2, None), _row(3, "")]))
    assert day["otherStatus"] == 1 and day["noStatus"] == 2
    assert all(count == 0 for count in day["statusCounts"].values())                  # Open included: blank is not "Open"
    assert day["total"] == 3                                                           # still counted as calls


def test_each_call_is_counted_once_per_day_and_dates_are_respected():
    result = _run([_row(1, "Dropped", date="2026-10-01"), _row(2, "Dropped", date="2026-10-02"), _row(3, "Dropped", date="2026-11-05")],
                  date_to="2026-10-31")
    assert _day(result, "2026-10-01")["statusCounts"]["Dropped"] == 1
    assert _day(result, "2026-10-02")["statusCounts"]["Dropped"] == 1
    assert all(r["date"] <= "2026-10-31" for r in result["callLogStatusRows"])           # the November call is outside


def test_the_agent_filter_narrows_the_status_counts():
    rows = [_row(1, "Dropped", agent="Ravi"), _row(2, "Dropped", agent="Meena"), _row(3, "Blocker", agent="Meena")]
    everyone = _day(_run(rows))
    meena = _day(_run(rows, agent="Meena"))
    assert everyone["statusCounts"]["Dropped"] == 2 and meena["statusCounts"]["Dropped"] == 1
    assert meena["statusCounts"]["Blocker"] == 1 and meena["total"] == 2


def test_existing_row_fields_other_pages_may_use_are_still_returned():
    day = _day(_run([_row(1, "Dropped")]))
    assert {"vm", "avgQuality", "duration", "openLeads", "open", "successPC"} <= set(day)


def test_new_spellings_fill_their_columns_and_legacy_ones_land_in_the_new_column():
    day = _day(_run([_row(1, "Success GE"), _row(2, "Success - GE"), _row(3, "Initial Email Follow Up"), _row(4, "initial email followup"),
                     _row(5, "PC link shared"), _row(6, "Initial email sent."), _row(7, "PC link shared & Lost")]))
    counts = day["statusCounts"]
    assert counts["Success GE"] == 2 and counts["Initial Email Follow Up"] == 2 and counts["PC link shared"] == 1
    assert counts["Initial email sent (Fee Details shared)"] == 1
    assert day["otherStatus"] == 1                      # a Lost status is not a dropdown value: reported, not forced into a column
