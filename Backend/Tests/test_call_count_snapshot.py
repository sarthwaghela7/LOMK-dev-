"""Daily call-count snapshot and reconciliation.

Pure logic tests always run. Database tests need a disposable LOCAL PostgreSQL:
set SNAPSHOT_TEST_DATABASE_URL (e.g. postgresql+psycopg2://user@127.0.0.1:5544/snaptest).
Each test uses a fresh schema; the production DATABASE_URL is never used.
"""
import importlib.util
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services import call_count_snapshot as snap  # noqa: E402
from services.call_count_snapshot import Counts, Member  # noqa: E402

CT = ZoneInfo("America/Chicago")
DAY = date(2026, 10, 8)
AFTER_CLOSE = datetime(2026, 10, 8, 17, 6, tzinfo=CT)


def counts(agents, extensions=None, members=None):
    return Counts(total=sum(agents.values()), by_agent=dict(agents),
                  by_extension=dict(extensions if extensions is not None else {}), members=list(members or []))


def row(result, dimension, key):
    return next(r for r in result["rows"] if r["dimension"] == dimension and r["key"] == key)


# --------------------------------------------------------------------------
# Pure comparison (tests 1-4, 8)
# --------------------------------------------------------------------------
def test_normal_day_matches():
    result = snap.compare({"total": {"TOTAL": 140}, "agent": {"101": 73, "102": 67}}, counts({"101": 73, "102": 67}))
    assert result["status"] == snap.MATCH and result["mismatches"] == 0


def test_agent_increase_is_a_mismatch():
    result = snap.compare({"total": {"TOTAL": 73}, "agent": {"101": 73}}, counts({"101": 74}))
    assert result["status"] == snap.MISMATCH
    assert row(result, "agent", "101")["difference"] == 1
    assert row(result, "total", "TOTAL")["difference"] == 1


def test_agent_decrease_is_a_mismatch():
    result = snap.compare({"total": {"TOTAL": 73}, "agent": {"101": 73}}, counts({"101": 72}))
    assert result["status"] == snap.MISMATCH and row(result, "agent", "101")["difference"] == -1


def test_total_unchanged_but_agents_changed_is_a_mismatch():
    result = snap.compare({"total": {"TOTAL": 140}, "agent": {"101": 73, "102": 67}}, counts({"101": 74, "102": 66}))
    assert row(result, "total", "TOTAL")["status"] == snap.MATCH
    assert row(result, "agent", "101")["status"] == snap.MISMATCH
    assert row(result, "agent", "102")["status"] == snap.MISMATCH
    assert result["status"] == snap.MISMATCH


def test_agent_with_zero_calls_is_kept_and_missing_key_counts_as_zero():
    result = snap.compare({"total": {"TOTAL": 5}, "agent": {"101": 5, "Edgin": 0}}, counts({"101": 5, "Edgin": 0, "New": 0}))
    assert row(result, "agent", "Edgin")["status"] == snap.MATCH
    assert row(result, "agent", "New")["status"] == snap.MATCH  # agent added after the snapshot, still 0
    again = snap.compare({"total": {"TOTAL": 5}, "agent": {"101": 5}}, counts({"101": 5, "102": 1}))
    assert row(again, "agent", "102")["difference"] == 1 and again["status"] == snap.MISMATCH


def test_extension_differences_are_informational_only():
    snapshot = {"total": {"TOTAL": 5}, "agent": {"101": 5}, "extension": {"+1555": 5}}
    result = snap.compare(snapshot, counts({"101": 5}, {"101": 5}))  # recording linked: extension rewritten
    assert result["status"] == snap.MATCH and result["mismatches"] == 0
    assert row(result, "extension", "+1555")["status"] == snap.MISMATCH and row(result, "extension", "+1555")["informational"]
    assert not row(result, "agent", "101")["informational"]


def test_na_sorts_last():
    result = snap.compare({"agent": {"N/A": 3, "101": 1}}, counts({"N/A": 3, "101": 1}))
    keys = [r["key"] for r in result["rows"] if r["dimension"] == "agent"]
    assert keys == ["101", "N/A"]


# --------------------------------------------------------------------------
# Timezone and weekends (test 7)
# --------------------------------------------------------------------------
def test_business_date_uses_chicago_not_utc():
    assert snap.business_today(datetime(2026, 10, 9, 0, 30, tzinfo=timezone.utc)) == date(2026, 10, 8)  # 7:30 PM CDT
    assert snap.business_today(datetime(2026, 10, 9, 5, 30, tzinfo=timezone.utc)) == date(2026, 10, 9)  # 12:30 AM CDT


def test_close_time_follows_daylight_saving():
    summer, winter = snap.close_time(date(2026, 10, 30)), snap.close_time(date(2026, 11, 2))  # DST ends Nov 1
    assert summer.utcoffset().total_seconds() == -5 * 3600
    assert winter.utcoffset().total_seconds() == -6 * 3600
    assert summer.astimezone(timezone.utc).hour == 22 and winter.astimezone(timezone.utc).hour == 23


def test_day_of_week_setting(monkeypatch):
    monkeypatch.delenv("CALL_SNAPSHOT_DAY_OF_WEEK", raising=False)
    assert snap.is_snapshot_day(date(2026, 10, 8)) and snap.is_snapshot_day(date(2026, 10, 9))
    assert not snap.is_snapshot_day(date(2026, 10, 10)) and not snap.is_snapshot_day(date(2026, 10, 11))
    monkeypatch.setenv("CALL_SNAPSHOT_DAY_OF_WEEK", "mon-sat")
    assert snap.is_snapshot_day(date(2026, 10, 10))
    monkeypatch.setenv("CALL_SNAPSHOT_DAY_OF_WEEK", "nonsense")
    assert snap.snapshot_days_setting() == "mon-fri"


# --------------------------------------------------------------------------
# PostgreSQL integration
# --------------------------------------------------------------------------
@pytest.fixture
def engine():
    url = os.getenv("SNAPSHOT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set SNAPSHOT_TEST_DATABASE_URL to run PostgreSQL integration tests")
    assert make_url(url).host in {"localhost", "127.0.0.1"}, "Tests require local PostgreSQL"
    schema = "test_snap_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = create_engine(url, connect_args={"options": f"-c search_path={schema}"})
    stub = ModuleType("storage.db")
    stub.engine = None
    previous = sys.modules.get("storage.db")
    sys.modules["storage.db"] = stub
    try:
        spec = importlib.util.spec_from_file_location("mig065", ROOT / "migrations" / "065_create_call_count_snapshots.py")
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
    finally:
        if previous is None:
            sys.modules.pop("storage.db", None)
        else:
            sys.modules["storage.db"] = previous
    with eng.begin() as conn:
        migration.create_tables(conn)
    try:
        yield eng
    finally:
        eng.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def members(*triples):
    return [Member(str(uuid.uuid5(uuid.NAMESPACE_URL, ref)), ref, agent, ext) for ref, agent, ext in triples]


BASE = members(("AF#1", "Priyanka", "201"), ("AF#2", "Priyanka", "201"), ("AF#3", "Gracy", "205"), ("AF#4", "N/A", "N/A"))


def base_counts(extra=()):
    allm = BASE + list(extra)
    by_agent, by_ext = {}, {}
    for m in allm:
        by_agent[m.agent] = by_agent.get(m.agent, 0) + 1
        by_ext[m.extension] = by_ext.get(m.extension, 0) + 1
    by_agent.setdefault("Edgin", 0)  # registered agent with no calls
    return Counts(total=len(allm), by_agent=by_agent, by_extension=by_ext, members=allm)


def stored(engine, table, where="TRUE"):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar()


def test_snapshot_saves_every_agent_and_total(engine):
    result = snap.take_snapshot(DAY, engine=engine, compute=lambda d: base_counts(), now=AFTER_CLOSE)
    assert result["outcome"] == "created" and result["total_calls"] == 4 and result["late"] is False
    with engine.connect() as conn:
        agents = dict(conn.execute(text("SELECT key, call_count FROM fd_call_count_snapshots WHERE dimension='agent'")).all())
        total = conn.execute(text("SELECT call_count FROM fd_call_count_snapshots WHERE dimension='total'")).scalar()
    assert agents == {"Priyanka": 2, "Gracy": 1, "N/A": 1, "Edgin": 0} and total == 4
    assert stored(engine, "fd_call_count_snapshot_calls") == 4


def test_unchanged_day_reconciles_as_match(engine):
    snap.take_snapshot(DAY, engine=engine, compute=lambda d: base_counts(), now=AFTER_CLOSE)
    result = snap.reconcile_date(DAY, engine=engine, compute=lambda d: base_counts())
    assert result["status"] == snap.MATCH
    assert [i["status"] for i in snap.history(engine=engine, now=AFTER_CLOSE) if i["business_date"] == "2026-10-08"] == [snap.MATCH]


def test_late_call_keeps_snapshot_and_reports_mismatch(engine):
    snap.take_snapshot(DAY, engine=engine, compute=lambda d: base_counts(), now=AFTER_CLOSE)
    late = members(("AF#99", "Gracy", "205"))
    result = snap.reconcile_date(DAY, engine=engine, compute=lambda d: base_counts(late))
    assert result["status"] == snap.MISMATCH
    assert row(result, "agent", "Gracy")["snapshot"] == 1 and row(result, "agent", "Gracy")["current"] == 2
    assert row(result, "total", "TOTAL")["difference"] == 1
    # snapshot rows untouched
    with engine.connect() as conn:
        assert conn.execute(text("SELECT call_count FROM fd_call_count_snapshots WHERE dimension='total'")).scalar() == 4
    detail = snap.affected_calls(DAY, agent="Gracy", engine=engine, compute=lambda d: base_counts(late))
    assert [(c["call_reference"], c["change"]) for c in detail["calls"]] == [("AF#99", "Added after snapshot")]
    assert [i["status"] for i in snap.history(engine=engine, now=AFTER_CLOSE)][0] == snap.MISMATCH


def test_reattribution_with_unchanged_total_is_detected(engine):
    snap.take_snapshot(DAY, engine=engine, compute=lambda d: base_counts(), now=AFTER_CLOSE)
    moved = [Member(BASE[0].call_id, "AF#1", "Gracy", "205")] + BASE[1:]
    current = Counts(total=4, by_agent={"Priyanka": 1, "Gracy": 2, "N/A": 1, "Edgin": 0},
                     by_extension={"201": 1, "205": 2, "N/A": 1}, members=moved)
    result = snap.reconcile_date(DAY, engine=engine, compute=lambda d: current)
    assert row(result, "total", "TOTAL")["status"] == snap.MATCH
    assert result["status"] == snap.MISMATCH
    detail = snap.affected_calls(DAY, engine=engine, compute=lambda d: current)
    assert [c["change"] for c in detail["calls"]] == ["Re-attributed after snapshot"]
    assert detail["calls"][0]["snapshot_agent"] == "Priyanka" and detail["calls"][0]["current_agent"] == "Gracy"


def test_running_twice_does_not_duplicate_or_overwrite(engine):
    first = snap.take_snapshot(DAY, engine=engine, compute=lambda d: base_counts(), now=AFTER_CLOSE)
    second = snap.take_snapshot(DAY, engine=engine, compute=lambda d: base_counts(members(("AF#99", "Gracy", "205"))), now=AFTER_CLOSE)
    assert first["outcome"] == "created" and second["outcome"] == "exists"
    assert stored(engine, "fd_call_count_snapshot_runs") == 1
    assert stored(engine, "fd_call_count_snapshots", "dimension='total'") == 1
    assert stored(engine, "fd_call_count_snapshots", "dimension='total' AND call_count=4") == 1


def test_concurrent_runs_create_exactly_one_snapshot(engine):
    def run(_):
        return snap.take_snapshot(DAY, engine=engine, compute=lambda d: base_counts(), now=AFTER_CLOSE)["outcome"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(run, range(6)))
    assert outcomes.count("created") == 1 and outcomes.count("exists") == 5
    assert stored(engine, "fd_call_count_snapshot_runs") == 1
    assert stored(engine, "fd_call_count_snapshots", "dimension='total'") == 1


def test_failed_snapshot_creates_no_snapshot_and_can_be_retried(engine):
    def boom(_):
        raise RuntimeError("database went away")
    failed = snap.take_snapshot(DAY, engine=engine, compute=boom, now=AFTER_CLOSE)
    assert failed["outcome"] == "failed" and "database went away" in failed["error"]
    assert stored(engine, "fd_call_count_snapshots") == 0 and stored(engine, "fd_call_count_snapshot_calls") == 0
    assert snap.reconcile_date(DAY, engine=engine, compute=lambda d: base_counts())["status"] == snap.SNAPSHOT_FAILED
    assert snap.history(engine=engine, now=AFTER_CLOSE)[0]["status"] == snap.SNAPSHOT_FAILED
    retry = snap.take_snapshot(DAY, engine=engine, compute=lambda d: base_counts(), now=AFTER_CLOSE)
    assert retry["outcome"] == "created"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT status, attempts FROM fd_call_count_snapshot_runs")).one() == ("completed", 2)


def test_reconciliation_error_is_never_reported_as_match(engine):
    snap.take_snapshot(DAY, engine=engine, compute=lambda d: base_counts(), now=AFTER_CLOSE)
    def boom(_):
        raise RuntimeError("timeout")
    result = snap.reconcile_date(DAY, engine=engine, compute=boom)
    assert result["status"] == snap.RECONCILIATION_ERROR and "timeout" in result["error"]
    assert snap.history(engine=engine, now=AFTER_CLOSE)[0]["status"] == snap.RECONCILIATION_ERROR


def test_not_yet_snapshotted_and_day_not_closed(engine):
    assert snap.reconcile_date(DAY, engine=engine, compute=lambda d: base_counts())["status"] == snap.NOT_SNAPSHOTTED
    early = datetime(2026, 10, 8, 16, 0, tzinfo=CT)
    assert snap.take_snapshot(DAY, engine=engine, compute=lambda d: base_counts(), now=early)["outcome"] == "not_closed"
    assert snap.take_snapshot(date(2026, 10, 9), engine=engine, compute=lambda d: base_counts(), now=AFTER_CLOSE)["outcome"] == "not_closed"
    assert stored(engine, "fd_call_count_snapshot_runs") == 0
    assert snap.history(engine=engine, now=AFTER_CLOSE)[0] == {"business_date": "2026-10-08", "status": snap.NOT_SNAPSHOTTED}


def test_dst_boundary_uses_central_close_time(engine):
    # 2026-11-02 22:30 UTC is 16:30 CST: not closed. 23:30 UTC is 17:30 CST: closed.
    day = date(2026, 11, 2)
    before = datetime(2026, 11, 2, 22, 30, tzinfo=timezone.utc)
    after = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)
    assert snap.take_snapshot(day, engine=engine, compute=lambda d: base_counts(), now=before)["outcome"] == "not_closed"
    assert snap.take_snapshot(day, engine=engine, compute=lambda d: base_counts(), now=after)["outcome"] == "created"


def test_snapshot_taken_after_midnight_is_flagged_late(engine):
    next_morning = datetime(2026, 10, 9, 8, 0, tzinfo=CT)
    assert snap.take_snapshot(DAY, engine=engine, compute=lambda d: base_counts(), now=next_morning)["late"] is True


def test_snapshot_rows_are_immutable(engine):
    snap.take_snapshot(DAY, engine=engine, compute=lambda d: base_counts(), now=AFTER_CLOSE)
    for statement in ("UPDATE fd_call_count_snapshots SET call_count = 999",
                      "DELETE FROM fd_call_count_snapshots",
                      "UPDATE fd_call_count_snapshot_calls SET agent = 'X'",
                      "DELETE FROM fd_call_count_snapshot_calls"):
        with pytest.raises(DBAPIError):
            with engine.begin() as conn:
                conn.execute(text(statement))
    assert stored(engine, "fd_call_count_snapshots", "dimension='total' AND call_count=4") == 1


def test_reconcile_recent_checks_only_closed_snapshots(engine):
    snap.take_snapshot(DAY, engine=engine, compute=lambda d: base_counts(), now=AFTER_CLOSE)
    today_run = datetime(2026, 10, 9, 6, 30, tzinfo=CT)
    summary = snap.reconcile_recent(engine=engine, compute=lambda d: base_counts(members(("AF#99", "Gracy", "205"))), now=today_run)
    assert summary == {"checked": 1, "match": 0, "mismatch": 1, "error": 0}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT last_check_status, last_check_mismatches > 0 FROM fd_call_count_snapshot_runs")).one() == ("MISMATCH", True)
