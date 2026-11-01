"""Integration tests: isolated local PostgreSQL only; never call an AI provider.

Set ANALYSIS_TEST_DATABASE_URL to a disposable localhost PostgreSQL database.
Each test uses a fresh schema, and the production DATABASE_URL is never used.
"""
import importlib
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.analysis_jobs import (
    AnalysisBusy, AnalysisJobStore, AnalysisNeedsReview, execute_analysis,
)


@pytest.fixture
def store(monkeypatch):
    url = os.getenv("ANALYSIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set ANALYSIS_TEST_DATABASE_URL to run PostgreSQL integration tests")
    assert make_url(url).host in {"localhost", "127.0.0.1"}, "Tests require local PostgreSQL"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("OPENAI_LIMIT_PROBE_ENABLED", "false")
    schema = "test_analysis_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema}"})
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE calls (call_id UUID PRIMARY KEY, fd_processing_status TEXT, result TEXT)"))
            conn.execute(text("CREATE TABLE call_analysis_results (call_id UUID, processing_status TEXT)"))
        # Import with a stub storage.db so configuration cannot probe a real DB.
        from types import ModuleType
        stub = ModuleType("storage.db")
        stub.engine = engine
        with monkeypatch.context() as imports:
            imports.setitem(sys.modules, "storage.db", stub)
            migration = importlib.import_module("migrations.046_analysis_jobs")
        monkeypatch.setattr(migration, "engine", engine)
        migration.migrate_analysis_jobs()
        migration.migrate_analysis_jobs()  # Deployment migration is repeatable.
        yield AnalysisJobStore(sessionmaker(bind=engine))
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def new_call(store):
    call_id = str(uuid.uuid4())
    with store.sessions.begin() as db:
        db.execute(text("INSERT INTO calls VALUES (:id, 'not processed', NULL)"), {"id": call_id})
    return call_id


def save_call(call_id, app):
    def apply(db, response):
        db.execute(text("UPDATE calls SET result=:result WHERE call_id=:id"), {"id": call_id, "result": response["content"]})
        if app == "flintdesk":
            db.execute(text("UPDATE calls SET fd_processing_status='processed' WHERE call_id=:id"), {"id": call_id})
        else:
            db.execute(text("INSERT INTO call_analysis_results VALUES (:id, 'completed')"), {"id": call_id})
    return apply


@pytest.mark.parametrize("app", ["autoflint", "flintdesk"])
def test_concurrent_workers_send_one_request(store, app):
    call_id = new_call(store)
    entered, release = Event(), Event()
    def response():
        entered.set()
        assert release.wait(5)
        return {"content": "saved"}
    generate = Mock(side_effect=response)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(execute_analysis, store, call_id, app, generate, save_call(call_id, app))
        try:
            assert entered.wait(5)
            # Separate sessions emulate another process or the manual endpoint.
            with pytest.raises(AnalysisBusy):
                execute_analysis(store, call_id, app, generate, save_call(call_id, app))
        finally:
            release.set()
        assert first.result()
    assert not execute_analysis(store, call_id, app, generate, save_call(call_id, app))
    assert generate.call_count == 1


@pytest.mark.parametrize("app", ["autoflint", "flintdesk"])
def test_failed_save_rolls_back_then_reuses_durable_response(store, app):
    call_id = new_call(store)
    generate = Mock(return_value={"content": "saved", "usage": {"total_tokens": 123}})
    def fail(db, response):
        save_call(call_id, app)(db, response)
        raise RuntimeError("Simulated save failure")
    with pytest.raises(RuntimeError):
        execute_analysis(store, call_id, app, generate, fail)
    with store.sessions() as db:
        assert db.execute(text("SELECT result FROM calls WHERE call_id=:id"), {"id": call_id}).scalar() is None
        job = db.execute(text("SELECT status, response FROM analysis_jobs")).mappings().one()
        assert job["status"] == "response_ready"
        assert job["response"]["usage"]["total_tokens"] == 123
    # A newly constructed coordinator represents recovery after process restart.
    recovered = AnalysisJobStore(store.sessions)
    assert execute_analysis(recovered, call_id, app, generate, save_call(call_id, app))
    assert generate.call_count == 1


def test_uncertain_timeout_never_automatically_retries(store):
    call_id = new_call(store)
    generate = Mock(side_effect=TimeoutError("Unknown provider outcome"))
    with pytest.raises(TimeoutError):
        execute_analysis(store, call_id, "flintdesk", generate, Mock())
    with pytest.raises(AnalysisNeedsReview):
        execute_analysis(store, call_id, "flintdesk", generate, Mock())
    assert generate.call_count == 1


def test_crashed_worker_is_not_reclaimed_for_generation(store):
    call_id = new_call(store)
    job_id, _ = store.acquire(call_id, "flintdesk")
    with store.sessions.begin() as db:
        db.execute(text("UPDATE analysis_jobs SET updated_at=now()-interval '1 hour'"))
    with pytest.raises(AnalysisNeedsReview):
        store.acquire(call_id, "flintdesk")
    # A slow original worker can still checkpoint a known response safely.
    store.checkpoint(job_id, {"content": "late answer"})
    assert store.acquire(call_id, "flintdesk") == (job_id, False)


def test_checkpoint_failure_blocks_paid_retry(store, monkeypatch):
    call_id = new_call(store)
    generate = Mock(return_value={"content": "saved"})
    monkeypatch.setattr(store, "checkpoint", Mock(side_effect=RuntimeError("DB unavailable")))
    with pytest.raises(RuntimeError):
        execute_analysis(store, call_id, "autoflint", generate, Mock())
    with pytest.raises(AnalysisNeedsReview):
        execute_analysis(store, call_id, "autoflint", generate, Mock())
    assert generate.call_count == 1


def test_explicit_reanalysis_is_idempotent_and_apps_are_independent(store):
    call_id = new_call(store)
    generate = Mock(return_value={"content": "saved"})
    for app in ["autoflint", "flintdesk"]:
        assert execute_analysis(store, call_id, app, generate, save_call(call_id, app))
    request_key = str(uuid.uuid4())
    assert execute_analysis(store, call_id, "flintdesk", generate, save_call(call_id, "flintdesk"), request_key)
    assert not execute_analysis(store, call_id, "flintdesk", generate, save_call(call_id, "flintdesk"), request_key)
    # Even after a later reanalysis, replaying an old request ID is free.
    assert execute_analysis(store, call_id, "flintdesk", generate, save_call(call_id, "flintdesk"), str(uuid.uuid4()))
    assert not execute_analysis(store, call_id, "flintdesk", generate, save_call(call_id, "flintdesk"), request_key)
    assert generate.call_count == 4


def test_simultaneous_reanalysis_cannot_bypass_active_job(store):
    call_id = new_call(store)
    store.acquire(call_id, "flintdesk")
    with pytest.raises(AnalysisBusy):
        store.acquire(call_id, "flintdesk", str(uuid.uuid4()))


def test_two_recovery_workers_apply_once(store):
    call_id = new_call(store)
    job_id, _ = store.acquire(call_id, "flintdesk")
    store.checkpoint(job_id, {"content": "saved"})
    apply = Mock(side_effect=save_call(call_id, "flintdesk"))
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: store.persist(job_id, apply), range(2)))
    assert apply.call_count == 1


def test_transient_checkpoint_failure_retries_database_only(store):
    from sqlalchemy import event
    call_id = new_call(store)
    generate = Mock(return_value={"content": "saved"})
    engine = store.sessions.kw["bind"]
    attempts = []
    def fail_once(conn, cursor, statement, parameters, context, executemany):
        if "SET response=" in statement:
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError("Transient write failure")
    event.listen(engine, "before_cursor_execute", fail_once)
    try:
        assert execute_analysis(store, call_id, "flintdesk", generate, save_call(call_id, "flintdesk"))
    finally:
        event.remove(engine, "before_cursor_execute", fail_once)
    assert len(attempts) == 2
    assert generate.call_count == 1


def test_manual_endpoint_and_background_share_admission(store, monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from services.Database_sync import f3
    from routes.flintdesk import calls
    call_id = new_call(store)
    record = SimpleNamespace(call_id=call_id, transcript="Synthetic transcript")
    processor = f3.F3Processor.__new__(f3.F3Processor)
    processor.application = "flintdesk"
    entered, release = Event(), Event()
    def generate(*args):
        entered.set()
        assert release.wait(5)
        return {"content": "saved"}
    processor.llm = SimpleNamespace(request_analysis=Mock(side_effect=generate), parse_response=lambda response, record: response)
    processor._persist_flintdesk_result = lambda record, result, db: save_call(call_id, "flintdesk")(db, result)
    monkeypatch.setattr(f3, "SessionLocal", store.sessions)
    monkeypatch.setattr(calls, "F3Processor", lambda **kw: processor)
    monkeypatch.setattr(calls, "_get_call_or_404", lambda *args: record)
    monkeypatch.setattr(calls, "_assert_call_access", lambda *args: None)
    request_db = Mock()
    with ThreadPoolExecutor(1) as pool:
        background = pool.submit(processor.process_record, record, raise_errors=True)
        try:
            assert entered.wait(5)
            with pytest.raises(HTTPException) as caught:
                calls.analyse_flintdesk_call(call_id, user={}, db=request_db)
            assert caught.value.status_code == 409
        finally:
            release.set()
        assert background.result() is True
    # Viewing/analysing a completed result through the endpoint is free.
    calls.analyse_flintdesk_call(call_id, user={}, db=request_db)
    assert processor.llm.request_analysis.call_count == 1
    request_db.commit.assert_not_called()  # Route cannot overwrite AutoFlint's status.


def test_pipeline_persistence_uses_callers_transaction(store, monkeypatch):
    from services.Database_sync import f3
    from storage.models import CallAnalysisResult
    from types import SimpleNamespace
    record = SimpleNamespace(call_id=uuid.uuid4())
    for app in ["autoflint", "flintdesk"]:
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = record
        processor = f3.F3Processor.__new__(f3.F3Processor)
        processor.application = app
        processor._get_field_keys = lambda: ["call_summary"] if app == "autoflint" else ["fd_a_call_summary"]
        result = CallAnalysisResult()
        processor._get_or_create_analysis_record = lambda *args: result
        method = processor._persist_autoflint_result if app == "autoflint" else processor._persist_flintdesk_result
        method(record, {}, db=db)
        db.commit.assert_not_called()
        db.close.assert_not_called()
        if app == "flintdesk":
            assert result.processing_status is None


def test_autoflint_skips_completed_uuid_returned_by_database(store, monkeypatch):
    from services.Database_sync import f3
    from storage.models import Call, CallAnalysisResult
    from types import SimpleNamespace
    call_id = new_call(store)
    with store.sessions.begin() as db:
        db.execute(text("INSERT INTO call_analysis_results VALUES (:id, 'completed')"), {"id": call_id})
    session = store.sessions()
    # Production's column is UUID although the ORM declares Text. psycopg2
    # returns UUID objects; comparing str(call_id) to these misses completion.
    completed_id = session.query(CallAnalysisResult.call_id).first()[0]
    assert isinstance(completed_id, uuid.UUID)
    candidate = SimpleNamespace(call_id=uuid.UUID(call_id))
    query = Mock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.all.return_value = [candidate]
    class SessionProxy:
        def query(self, target):
            return query if target is Call else session.query(target)
        def execute(self, *args, **kwargs):
            return session.execute(*args, **kwargs)
        def close(self):
            session.close()
    monkeypatch.setattr(f3, "SessionLocal", SessionProxy)
    processor = f3.F3Processor.__new__(f3.F3Processor)
    processor.application = "autoflint"
    assert processor.fetch_transcript_ready_calls() == []
