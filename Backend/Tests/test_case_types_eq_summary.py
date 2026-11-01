"""The Case Types + EQ response carries the time it was read (Central Time) and the call-record counts for that moment."""
from datetime import date, datetime

from services import dashboard_summary as ds
from services import case_type_trends as T


def _row(day, ref, case, question, ctype="New Client - Individual"):
    return {"day": day, "call_ref": ref, "t1": case, "t2": None, "question": question, "client_type": ctype}


def test_summary_counts_match_the_calls_page_and_the_table(monkeypatch):
    rows = [_row(date(2026, 10, 6), "AF#1", "H-1B", "q1"), _row(date(2026, 10, 7), "AF#2", "", "q2"),
            _row(date(2026, 10, 7), "AF#3", "H-1B", "", ), _row(date(2026, 10, 8), "AF#4", "H-1B", "vm", "Voicemail Call")]
    calls = []

    def fake_read(db, **kw):
        calls.append(kw)
        if kw.get("only_unanalyzed"):
            assert kw.get("call_source") and kw.get("ids_only") and kw["target_page"] == "calls"
            return [(11, date(2026, 10, 8)), (12, date(2026, 10, 8))]
        assert kw["target_page"] == "calls" and kw["page"] == 1 and kw["page_size"] == 1
        return [], {}, 937
    import services.analysis_read_query as arq
    monkeypatch.setattr(arq, "analysis_read", fake_read)
    monkeypatch.setattr(T, "fetch_call_rows", lambda db, s, e, with_hour=False: rows)
    out = ds.build_case_types_eq(None, None, date(2026, 10, 1), date(2026, 10, 31))
    summary = out["summary"]
    assert summary["analysedRecords"] == 937 and summary["waitingForAnalysis"] == 2 and summary["callRecords"] == 939
    assert summary["enquiryQuestions"] == 2                                         # q1, q2
    assert summary["voicemailOrSpam"] == 1 and summary["noQuestionRecorded"] == 1   # the voicemail row; the blank question
    # Every call record in range is accounted for exactly once.
    assert (summary["enquiryQuestions"] + summary["voicemailOrSpam"] + summary["noQuestionRecorded"]
            + summary["otherRecords"] + summary["waitingForAnalysis"]) == summary["callRecords"] == 939
    assert sum(t["count"] for week in out["caseTypeEnquiryRows"] for t in week["types"]) == summary["enquiryQuestions"]
    assert summary["timezone"] == "America/Chicago"
    stamp = datetime.fromisoformat(summary["asOf"])
    assert stamp.utcoffset().total_seconds() in (-5 * 3600, -6 * 3600)              # Central time (CDT or CST), not UTC/IST
    assert len(calls) == 2


def test_existing_response_fields_are_unchanged(monkeypatch):
    import services.analysis_read_query as arq
    monkeypatch.setattr(arq, "analysis_read", lambda db, **kw: [] if kw.get("ids_only") else ([], {}, 0))
    monkeypatch.setattr(T, "fetch_call_rows", lambda db, s, e, with_hour=False: [])
    out = ds.build_case_types_eq(None, None, date(2026, 10, 1), date(2026, 10, 7))
    assert {"mode", "dateFrom", "dateTo", "caseTypeEnquiryRows"} <= set(out)
    assert out["summary"]["callRecords"] == 0 and out["summary"]["enquiryQuestions"] == 0
