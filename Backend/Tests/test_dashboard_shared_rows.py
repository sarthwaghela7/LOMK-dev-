import json
import shutil
import subprocess

import pytest

from services import dashboard_summary as ds

BUNDLE = ds.Path(ds.__file__).with_name("dashboard_calculator.mjs")


def _payload():
    analysis = [{"call_id": f"c{i}", "front_desk_person_name": "Asha", "fd_m_case_status_change": "Open"} for i in range(5)]
    calls = [{"call_id": f"c{i}", "call_date": "2026-10-20", "call_time": "10:00:00"} for i in range(5)]
    return {"dashboardAnalysis": analysis[:3], "analysis": analysis, "dashboardCalls": calls[:3], "calls": calls,
            "leads": [], "tasks": []}


def test_overlapping_rows_are_sent_once_and_every_list_keeps_its_order():
    shared = ds._share_rows(_payload())
    assert len(shared["rowPool"]) == 10
    assert shared["dashboardAnalysis"] == shared["analysis"][:3]
    assert shared["dashboardCalls"] == shared["calls"][:3]
    assert [shared["rowPool"][i] for i in shared["analysis"]] == _payload()["analysis"]
    assert [shared["rowPool"][i] for i in shared["calls"]] == _payload()["calls"]


def test_rows_that_differ_are_not_merged():
    payload = _payload()
    payload["dashboardAnalysis"][0] = {**payload["dashboardAnalysis"][0], "fd_m_case_status_change": "Closed"}
    shared = ds._share_rows(payload)
    assert len(shared["rowPool"]) == 11
    assert shared["rowPool"][shared["dashboardAnalysis"][0]]["fd_m_case_status_change"] == "Closed"
    assert shared["rowPool"][shared["analysis"][0]]["fd_m_case_status_change"] == "Open"


@pytest.mark.skipif(not shutil.which("node"), reason="node is required for the calculator")
def test_calculator_accepts_a_payload_over_the_old_32mib_cap_and_rebuilds_the_lists():
    payload = _payload()
    payload["dateFrom"] = payload["dateTo"] = "2026-10-20"
    shared = ds._share_rows(payload)
    shared["rowPool"].append({"filler": "x" * (33 * 1024 * 1024)})
    result = subprocess.run(["node", str(BUNDLE)], input=json.dumps(shared), capture_output=True, text=True,
                            env={"TZ": "America/Chicago", "PATH": ds.os.environ.get("PATH", "")}, timeout=60)
    assert "exceeds limit" not in result.stderr
    assert "rowPool" not in result.stderr
