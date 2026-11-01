from datetime import datetime, timezone

from services.dashboard_ops_metrics import (
    build_call_records, close_call_aging, conversion_duration, filter_call_records,
    leads_block, normalize_closed_status, parse_duration_seconds, tasks_block,
)


def lead(i, owner="Asha", ext="101", submitted=True, call=""):
    return {"id": i, "record": {"owner": owner, "extension": ext},
            "has_submitted_assignment": submitted, "same_day_outbound_call_id": call}


def task(i, kind, owner_id="u1", submitted="2026-10-01", name="Asha", **data):
    return {"id": i, "task_type": kind, "owner_id": owner_id, "owner_name": name,
            "submitted_at": submitted, "data": data}


def test_leads_assigned_needs_submission_and_called_back_is_green_only():
    rows = [lead(1, call="c1"), lead(2, submitted=False), lead(3, call="c3"), lead(1, call="c1")]
    assert leads_block(rows) == {"assigned": 2, "calledBack": 2}  # duplicate id counted once


def test_leads_filters_agent_number_extension():
    rows = [lead(1, "Asha", "101", call="x"), lead(2, "Ben", "201", call="y")]
    assert leads_block(rows, agent="Ben") == {"assigned": 1, "calledBack": 1}
    assert leads_block(rows, number="3128781255") == {"assigned": 1, "calledBack": 1}
    assert leads_block(rows, extension="999") == {"assigned": 0, "calledBack": 0}


def test_missed_calls_owner_without_submit_not_assigned_and_green_by_callback():
    rows = [task(1, "missed_call", callback={"call_id": "c"}),
            task(2, "missed_call", submitted=None, callback_matched=True),
            task(3, "missed_call", owner_id=None),
            task(4, "non_responsiveness", call_id="c9")]
    assert tasks_block(rows, "missed_call") == {"assigned": 1, "calledBack": 2}
    assert tasks_block(rows, "non_responsiveness") == {"assigned": 1, "calledBack": 1}
    assert tasks_block(rows, "missed_call", agent="Nobody") == {"assigned": 0, "calledBack": 0}


def test_duration_boundaries_voicemail_and_invalid():
    def rec(duration, vm=False):
        return {"duration": duration, "voicemail": vm}
    result = conversion_duration([rec("179"), rec("180"), rec("300"), rec("600"), rec("900"), rec("961"),
                                  rec("5:00"), rec("-"), rec(None), rec("1000", vm=True)])
    assert result["3"] == 6 and result["5"] == 5 and result["10"] == 3 and result["15"] == 2
    assert result["invalidDuration"] == 2 and result["eligible"] == 7
    assert parse_duration_seconds("-5") is None


def test_closed_status_normalisation_excludes_unrelated():
    assert normalize_closed_status(" Success - GE ") == "success - ge"
    assert normalize_closed_status("Success-PC") == "success - pc"
    assert normalize_closed_status("Success GE") == "success - ge"
    assert normalize_closed_status("DROPPED") == "dropped"
    assert normalize_closed_status("PC Follow Up") is None
    assert normalize_closed_status("Not Dropped") is None


def test_close_aging_uses_entry_to_stage3_dates():
    def rec(aid, status, created):
        return {"analysis_id": aid, "status": status, "created_at": created}
    t = lambda d: datetime(2026, 10, d, 18, tzinfo=timezone.utc)
    records = [rec(1, "Success - GE", "2026-10-01T18:00:00"), rec(2, "Dropped", "2026-10-01T18:00:00"),
               rec(3, "Open", "2026-10-01T18:00:00"), rec(4, "Success - PC", "2026-10-01T18:00:00"),
               rec(5, "Success - PC", None), rec(6, "Success - PC", "2026-10-20T18:00:00")]
    closed = {(1, "success - ge"): t(4), (2, "dropped"): t(20), (3, "success - ge"): t(9),
              (5, "success - pc"): t(9), (6, "success - pc"): t(9)}
    result = close_call_aging(records, closed)
    # 1: 3 days; 2: 19 days; 3 not closed (ignored); 4 no history; 5 no entry date; 6 closes before entry
    assert (result["under3"], result["d3to4"], result["d5to9"], result["d10to14"], result["d15plus"]) == (0, 1, 0, 0, 1)
    assert result["eligible"] == 2 and result["excludedMissingDates"] == 3


def test_call_population_dedupes_and_filters():
    analysis = [{"id": 1, "call_id": "a", "front_desk_person_name": "Asha", "src": "3128781255", "dst": "9"}]
    calls = [{"call_id": "a", "extension": "101"}, {"call_id": "b", "extension": "201", "src": "1", "dst": "2243374447"}]
    records = build_call_records(analysis, calls)
    assert [r["call_id"] for r in records] == ["a", "b"]
    assert [r["call_id"] for r in filter_call_records(records, agent="Asha")] == ["a"]
    assert [r["call_id"] for r in filter_call_records(records, number="2243374447")] == ["b"]
    assert [r["call_id"] for r in filter_call_records(records, extension="101")] == ["a"]
