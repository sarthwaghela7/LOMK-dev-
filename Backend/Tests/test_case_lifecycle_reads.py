"""Lifecycle filter + Dashboard counts through the real analysis_read query, on a disposable local PostgreSQL schema.

    CASE_LIFECYCLE_TEST_DATABASE_URL=postgresql+psycopg2://user@127.0.0.1:5432/lifecycle_test
"""
import os
import uuid
from datetime import date, time
from importlib import import_module

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from services import case_lifecycle as lc


@pytest.fixture
def db(monkeypatch):
    url = os.getenv("CASE_LIFECYCLE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set CASE_LIFECYCLE_TEST_DATABASE_URL to a disposable local PostgreSQL database")
    assert make_url(url).host in {"localhost", "127.0.0.1"}
    from storage.models import Base
    schema = "test_lcr_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema}"})
    for table in Base.metadata.sorted_tables:        # a few unrelated models carry a literal uuid default PostgreSQL rejects; skip those
        try:
            with engine.begin() as conn:
                table.create(conn)
        except Exception:  # noqa: BLE001
            pass
    module = import_module("migrations.073_case_lifecycle")
    monkeypatch.setattr(module, "engine", engine)
    module.migrate_case_lifecycle()
    wrong_number = import_module("migrations.076_wrong_number_dialed")
    monkeypatch.setattr(wrong_number, "engine", engine)
    wrong_number.migrate_wrong_number_dialed()
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


_clock = iter(range(0, 86400, 60))


def _call(db, status, day=date(2026, 10, 10), client="CL-1", analysed=True):
    """One call. Each gets its own second so the Calls page's physical-call de-duplication (same time/src/dst = one call
    heard on two legs) does not merge them; same client and same number are otherwise deliberately reused."""
    cid = uuid.uuid4()
    seconds = next(_clock)
    db.execute(text("INSERT INTO calls (call_id, client_id, call_date, call_time, src, dst) VALUES (:c, NULL, :d, :t, '3125550100', '2243939098')"),
               {"c": cid, "d": day, "t": time(seconds // 3600, seconds % 3600 // 60)})
    if analysed:
        db.execute(text("INSERT INTO call_analysis_results (call_id, fd_m_case_status_change, call_recording_date_normalized) VALUES (:c, :s, :d)"),
                   {"c": str(cid), "s": status, "d": day})
    db.commit()
    return cid


def test_counts_and_filter_use_the_persisted_stage_one_per_call(db):
    statuses = ["Open", "Open", "PC link shared", "PC link shared", "Success PC", "Dropped", "Client Side Delay", None]
    for status in statuses:
        _call(db, status)
    _call(db, None, analysed=False)                     # a call nobody analysed yet is Open
    found = lc.lifecycle_counts(db, start=date(2026, 10, 1), end=date(2026, 10, 31))
    # Open = blank + Open, Blocker, Same Client Context, Client Side Delay, Dropped: 2 Open + Dropped + CSD + blank + unanalysed
    assert found["counts"] == {"open": 6, "in_transition": 2, "lost": 0, "dropped": 2, "success": 1}
    assert found["total"] == 9                          # distinct calls; Open overlaps Dropped, so it is not the sum

    from services.analysis_read_query import analysis_read
    rows, _, total = analysis_read(db, start=date(2026, 10, 1), end=date(2026, 10, 31), lifecycle_stage="in_transition",
                                   page=1, page_size=1)
    assert total == 2 and len(rows) == 1 and rows[0].case_lifecycle_stage == "in_transition"
    rows, _, total = analysis_read(db, start=date(2026, 10, 1), end=date(2026, 10, 31), lifecycle_stage="dropped")
    assert {r.fd_m_case_status_change for r in rows} == {"Dropped", "Client Side Delay"}
    # The Open Cases button lists exactly what the card counts (analysed rows here; the unanalysed call comes from the other read)
    rows, _, total = analysis_read(db, start=date(2026, 10, 1), end=date(2026, 10, 31), lifecycle_stage="open", call_source=True)
    assert len(rows) == 6


def test_filters_combine_with_the_date_range(db):
    _call(db, "PC link shared", day=date(2026, 10, 10))
    _call(db, "PC link shared", day=date(2026, 9, 10))
    from services.analysis_read_query import analysis_read
    rows, _, total = analysis_read(db, start=date(2026, 10, 1), end=date(2026, 10, 31), lifecycle_stage="in_transition")
    assert len(rows) == 1
    assert lc.lifecycle_counts(db, start=date(2026, 9, 1), end=date(2026, 10, 31))["counts"]["in_transition"] == 2
