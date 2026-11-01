"""The scheduler job around services.priority_cases: off switch, overlap guard, and that errors never escape."""
from datetime import date

from scheduler import jobs
from services import priority_cases as pc


def test_worker_does_nothing_when_disabled(monkeypatch):
    monkeypatch.setenv("PRIORITY_CASES_ENABLED", "false")
    monkeypatch.setattr(pc, "run_batch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    jobs.run_priority_cases_worker()


def test_worker_runs_a_bounded_recent_window_and_yields_to_f3(monkeypatch):
    monkeypatch.delenv("PRIORITY_CASES_ENABLED", raising=False)
    seen = {}

    def fake_run_batch(session_factory, llm_factory, **kw):
        seen.update(kw)
        seen["f3_busy"] = kw["should_stop"]()           # the job must pass a callable that asks whether F3 is busy
        return {"eligible": 0}

    monkeypatch.setattr(pc, "run_batch", fake_run_batch)
    monkeypatch.setattr("services.dependency_ai.f3_is_busy", lambda db: True)
    monkeypatch.setattr("storage.db.SessionLocal", lambda: _NullSession())
    jobs.run_priority_cases_worker()
    assert seen["limit"] == pc.WORKER_BATCH_LIMIT and seen["f3_busy"] is True
    assert seen["reanalyse"] is False                   # the worker never re-analyses calls that already have a result
    assert seen["start"] <= seen["end"] and (seen["end"] - seen["start"]).days <= pc.WORKER_LOOKBACK_DAYS
    assert seen["start"] >= pc.start_date()


def test_worker_swallows_errors_and_releases_its_lock(monkeypatch):
    monkeypatch.setattr(pc, "run_batch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    jobs.run_priority_cases_worker()                     # must not raise
    assert jobs.PRIORITY_CASES_LOCK.acquire(blocking=False)
    jobs.PRIORITY_CASES_LOCK.release()


def test_overlapping_run_is_skipped(monkeypatch, capsys):
    monkeypatch.setattr(pc, "run_batch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert jobs.PRIORITY_CASES_LOCK.acquire(blocking=False)
    try:
        jobs.run_priority_cases_worker()
    finally:
        jobs.PRIORITY_CASES_LOCK.release()
    assert "already running" in capsys.readouterr().out


def test_window_never_starts_before_the_configured_start(monkeypatch):
    monkeypatch.setenv("PRIORITY_CASES_SINCE", "2026-10-10")
    assert pc.worker_window(date(2026, 10, 11)) == (date(2026, 10, 10), date(2026, 10, 11))
    assert pc.worker_window(date(2026, 10, 30))[0] == date(2026, 10, 27)


class _NullSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
