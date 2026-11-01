import sys
import time
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.Database_sync import f2, f3


def tracking_processor():
    state = {"active": 0, "peak": 0}
    guard = Lock()

    def process(record):
        with guard:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with guard:
            state["active"] -= 1
        return True

    return state, process


def session_for(record=None):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = record
    return db


def test_f2_processes_multiple_calls_concurrently(monkeypatch):
    records = [SimpleNamespace(call_id=index) for index in range(4)]
    processor = f2.F2Processor.__new__(f2.F2Processor)
    processor.db = None
    processor.fetch_unprocessed_records = lambda **kwargs: records
    processor.claim_calls = lambda ids: ids
    processor.close = lambda: None
    state, processor.process_record = tracking_processor()
    monkeypatch.setattr(f2, "SessionLocal", lambda: session_for())

    processor.run(max_workers=2)

    assert state["peak"] == 2


def test_f3_processes_multiple_calls_concurrently_and_detaches_before_io(monkeypatch):
    records = [SimpleNamespace(call_id=index, call_date=None, call_time=None) for index in range(4)]
    processor = f3.F3Processor.__new__(f3.F3Processor)
    processor.application = "flintdesk"
    processor.fetch_transcript_ready_calls = lambda **kwargs: records
    processor.claim_calls = lambda ids: ids
    state, processor.process_record = tracking_processor()
    sessions = []

    def new_session():
        record = records[len(sessions)]
        db = session_for(record)
        sessions.append(db)
        return db

    monkeypatch.setattr(f3, "SessionLocal", new_session)
    result = processor.run(max_workers=3)

    assert state["peak"] == 3
    assert result["processed"] == 4
    assert result["failed"] == 0
    assert all(db.expunge.called and db.close.called for db in sessions)


def test_stage_lock_skips_overlapping_job():
    from scheduler.jobs import skip_if_stages_busy

    lock = Lock()
    lock.acquire()
    called = []
    try:
        assert skip_if_stages_busy(lock)(lambda: called.append(True))() is None
    finally:
        lock.release()
    assert called == []
