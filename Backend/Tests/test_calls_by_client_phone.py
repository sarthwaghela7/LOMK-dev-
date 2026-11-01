from datetime import date

import pytest
from fastapi import HTTPException

from routes import calls


@pytest.mark.parametrize("phone", ["", "   ", "123", "abc", "12-34"])
def test_blank_or_too_short_phone_is_rejected_before_any_query(phone):
    # db=None proves no query is attempted for an invalid/broad request.
    with pytest.raises(HTTPException) as exc:
        calls.get_logs_by_client_phone(client_phone=phone, from_date=None, to_date=None, db=None, user={})
    assert exc.value.status_code == 422


def test_reversed_dates_are_rejected():
    with pytest.raises(HTTPException) as exc:
        calls.get_logs_by_client_phone(
            client_phone="(847) 443-5580", from_date=date(2026, 10, 2), to_date=date(2026, 10, 1), db=None, user={},
        )
    assert exc.value.status_code == 400


def test_dates_are_optional_and_phone_is_matched_like_the_calls_filter(monkeypatch):
    seen = {}

    def fake_analysis_read(db, **kwargs):
        seen.setdefault("calls", []).append(kwargs)
        return [], {}, None

    monkeypatch.setattr("services.analysis_read_query.analysis_read", fake_analysis_read)
    monkeypatch.setattr(calls, "serialize_analysis_rows", lambda *a, **k: [])
    monkeypatch.setattr(calls, "_serialize_call_records", lambda db, rows: [])
    monkeypatch.setattr(calls, "_missed_calls_page_count", lambda db, digits, start, end: 3)
    out = calls.get_logs_by_client_phone(client_phone="+1 (847) 443-5580", from_date=None, to_date=None, db=None, user={})
    assert out == {"items": [], "pending_calls": [], "missed_calls_count": 3}
    # Digits only go to the same client_number filter the Calls page uses.
    assert all(call["client_number"] == "18474435580" for call in seen["calls"])
    assert seen["calls"][0]["start"] == date(2000, 1, 1)
    assert seen["calls"][1]["only_unanalyzed"] is True
