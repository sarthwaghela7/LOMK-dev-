"""Case lifecycle: classification, per-call independence, the In-Transition timer, Lost processing and the Teams outbox.

Pure tests always run. The PostgreSQL tests need a DISPOSABLE local database (never the production DATABASE_URL):
    CASE_LIFECYCLE_TEST_DATABASE_URL=postgresql+psycopg2://user@127.0.0.1:5432/lifecycle_test
Each test gets its own schema.
"""
import os
import sys
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import case_lifecycle as lc  # noqa: E402
from services import case_lost_processor as lost  # noqa: E402

# ------------------------------------------------------------------ pure
CLASSIFICATION = [
    ("Open", "open"), ("Blocker", "open"), ("Same Client Context", "open"), (None, "open"), ("", "open"), ("   ", "open"),
    ("Initial email sent (Fee Details shared)", "in_transition"), ("Initial Email Follow Up", "in_transition"),
    ("PC link shared", "in_transition"), ("PC Follow Up", "in_transition"),
    ("Client Side Delay", "dropped"), ("Dropped", "dropped"), ("Wrong Number Dialed", "dropped"), (" wrong  number DIALED ", "dropped"),
    ("Success PC", "success"), ("Success GE", "success"),
    ("PC link shared & Lost", "lost"), ("Initial Email Follow Up & Lost", "lost"), ("PC Follow Up & Lost", "lost"),
    ("Fee details shared & FU", "in_transition"), ("Fee details shared & FU & Lost", "lost"),
    # case / spacing never matter
    (" pc  LINK shared ", "in_transition"), ("success pc", "success"),
    # legacy spellings stored before the dropdown change
    ("Success - PC", "success"), ("Success - GE", "success"), ("Initial email sent.", "in_transition"),
    ("initial email followup", "in_transition"), ("Fee details shared & FU", "in_transition"),
    ("PC shared and lost", "lost"), ("Move to next date", "open"), ("something unknown", "open"),
]


@pytest.mark.parametrize("status,stage", CLASSIFICATION)
def test_status_maps_to_its_stage(status, stage):
    assert lc.lifecycle_stage_of(status) == stage


def test_dropdown_has_exactly_the_thirteen_required_values_in_order():
    assert lc.CASE_STATUS_OPTIONS == (
        "Open", "Client Side Delay", "Blocker", "Dropped", "Same Client Context", "Wrong Number Dialed",
        "Initial email sent (Fee Details shared)", "Initial Email Follow Up", "Fee details shared & FU", "PC link shared",
        "PC Follow Up", "Success PC", "Success GE")
    assert len(set(lc.CASE_STATUS_OPTIONS)) == 13


def test_fee_details_shared_and_fu_is_its_own_in_transition_value():
    assert lc.lifecycle_stage_of("Fee details shared & FU") == "in_transition"
    assert lc.canonical_status("fee details shared & fu") == "Fee details shared & FU"
    assert lc.accepted_status("FEE DETAILS SHARED & FU") == "Fee details shared & FU"
    assert lc.lost_status_for("Fee details shared & FU") == "Fee details shared & FU & Lost"


def test_every_dropdown_value_has_a_stage_and_lost_is_never_a_dropdown_value():
    stages = {lc.lifecycle_stage_of(v) for v in lc.CASE_STATUS_OPTIONS}
    assert stages == {"open", "in_transition", "dropped", "success"}


@pytest.mark.parametrize("last", list(lc.IN_TRANSITION_STATUSES))
def test_lost_suffix_is_appended_exactly_once(last):
    once = lc.lost_status_for(last)
    assert once == last + " & Lost"
    assert lc.lost_status_for(once) == once


@pytest.mark.parametrize("legacy,new", list(lc.LEGACY_STATUS_ALIASES.items()))
def test_legacy_spellings_map_to_the_new_wording(legacy, new):
    assert lc.canonical_status(legacy) == new


def test_the_thirty_day_example_from_the_specification():
    entered = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
    eligible = lambda day: lc.is_lost_eligible(entered, day)  # noqa: E731
    assert not eligible(date(2026, 11, 19))       # still In-Transition (30 days)
    assert not eligible(date(2026, 11, 20))       # the 30-day boundary is reached: not yet Lost
    assert eligible(date(2026, 11, 21))           # Lost when the processor checks on this date
    assert lc.entered_on_or_before(date(2026, 11, 21)) == date(2026, 10, 20)


def test_boundary_uses_the_central_calendar_day():
    late_evening_central = datetime(2026, 10, 21, 3, 30, tzinfo=timezone.utc)   # 22:30 on 20 Oct in Chicago
    assert lc.central_date(late_evening_central) == date(2026, 10, 20)
    assert lc.is_lost_eligible(late_evening_central, date(2026, 11, 21))
    assert not lc.is_lost_eligible(late_evening_central, date(2026, 11, 20))


def test_teams_payload_has_no_client_details():
    rows = [{"call_id": str(uuid.uuid4()), "call_reference": f"AF#{i}", "previous_status": "PC link shared",
             "lost_status": "PC link shared & Lost", "transitioned_at": datetime.now(timezone.utc)} for i in range(10)]
    payload = lost.build_payload(uuid.uuid4(), 3, rows)
    assert payload["count"] == 10 and payload["sequence"] == 3 and payload["idempotency_key"] == payload["batch_id"]
    assert set(payload["calls"][0]) == {"call_id", "call_reference", "previous_status", "lost_status", "lost_at"}


# ------------------------------------------------------------------ PostgreSQL
@pytest.fixture
def world(monkeypatch):
    url = os.getenv("CASE_LIFECYCLE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set CASE_LIFECYCLE_TEST_DATABASE_URL to a disposable local PostgreSQL database")
    assert make_url(url).host in {"localhost", "127.0.0.1"}, "Tests require local PostgreSQL"
    schema = "test_lc_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema}"}, pool_size=10)
    with engine.begin() as conn:
        for ddl in (
            "CREATE TABLE calls (call_id uuid primary key, call_reference text, client_id text)",
            "CREATE TABLE call_analysis_results (id bigserial primary key, call_id text, fd_m_case_status_change text, fd_m_date_entry date)",
            "CREATE TABLE fd_case_status_history (id bigserial primary key, analysis_id bigint, call_id text, previous_status text,"
            " new_status text not null, changed_at timestamptz default now(), changed_by uuid)",
        ):
            conn.execute(text(ddl))
    module = import_module("migrations.073_case_lifecycle")
    monkeypatch.setattr(module, "engine", engine)
    module.migrate_case_lifecycle()
    module.migrate_case_lifecycle()          # idempotent: a second run changes nothing and does not fail
    wrong_number = import_module("migrations.076_wrong_number_dialed")
    monkeypatch.setattr(wrong_number, "engine", engine)
    wrong_number.migrate_wrong_number_dialed()
    wrong_number.migrate_wrong_number_dialed()          # idempotent too
    session_factory = sessionmaker(bind=engine)
    Session = session_factory

    class World:
        n = 0
        Session = staticmethod(session_factory)

        def log(self, status=None, client="CL-1", date_entry=None):
            self.n += 1
            call_id = uuid.uuid4()
            with Session() as db, db.begin():
                db.execute(text("INSERT INTO calls VALUES (:c, :r, :cl)"), {"c": call_id, "r": f"AF#{1000 + self.n}", "cl": client})
                return db.execute(text("INSERT INTO call_analysis_results (call_id, fd_m_case_status_change, fd_m_date_entry)"
                                       " VALUES (:c, :s, :d) RETURNING id"), {"c": str(call_id), "s": status, "d": date_entry}).scalar_one()

        def set_status(self, aid, status):
            with Session() as db, db.begin():
                db.execute(text("UPDATE call_analysis_results SET fd_m_case_status_change = :s WHERE id = :i"), {"s": status, "i": aid})

        def row(self, aid):
            with Session() as db:
                return dict(db.execute(text("SELECT * FROM call_analysis_results WHERE id = :i"), {"i": aid}).mappings().one())

        def age(self, aid, days):
            """Pretend the call entered In-Transition `days` days ago (direct write, like a backfill)."""
            with Session() as db, db.begin():
                db.execute(text("UPDATE call_analysis_results SET in_transition_entered_at = now() - make_interval(days => :d) WHERE id = :i"),
                           {"d": days, "i": aid})

        def counts(self):
            with Session() as db:
                rows = db.execute(text("SELECT case_lifecycle_stage, count(*) FROM call_analysis_results GROUP BY 1")).all()
            return {stage: n for stage, n in rows}

        def scalar(self, sql, **p):
            with Session() as db:
                return db.execute(text(sql), p).scalar()

    try:
        yield World()
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_sql_function_agrees_with_the_python_classifier(world):
    for status, stage in CLASSIFICATION:
        assert world.scalar("SELECT fd_case_lifecycle_stage_of(:s)", s=status) == stage, status


@pytest.mark.parametrize("status,stage", CLASSIFICATION)
def test_new_records_are_categorised_on_insert(world, status, stage):
    assert world.row(world.log(status))["case_lifecycle_stage"] == stage


def test_five_logs_of_one_client_are_counted_independently(world):
    ids = [world.log(s, client="CL-1") for s in ("Open", "Open", "PC link shared", "PC link shared", "Success PC")]
    assert world.counts() == {"open": 2, "in_transition": 2, "success": 1}
    assert sum(world.counts().values()) == 5            # nothing is de-duplicated by client or context
    assert world.row(ids[0])["case_lifecycle_stage"] == "open"


def test_a_change_moves_only_that_call(world):
    a, b, c = (world.log("Open") for _ in range(3))
    world.set_status(a, "PC link shared")
    assert world.counts() == {"open": 2, "in_transition": 1}
    world.set_status(a, "Success GE")
    assert world.counts() == {"open": 2, "success": 1}                # Success, no longer In-Transition
    world.set_status(b, "Dropped")
    assert world.counts() == {"open": 1, "success": 1, "dropped": 1}


def test_propagation_style_bulk_update_recategorises_exactly_the_updated_rows(world):
    ids = [world.log("Open") for _ in range(5)]
    with world.Session() as db, db.begin():      # what the existing closure confirmation does: plain SQL on chosen rows
        db.execute(text("UPDATE call_analysis_results SET fd_m_case_status_change = 'Success PC' WHERE id = ANY(:ids)"), {"ids": ids[:3]})
    assert world.counts() == {"success": 3, "open": 2}


def test_timer_starts_at_entry_and_is_not_reset_by_edits_or_other_calls(world):
    a = world.log("Open")
    world.set_status(a, "PC link shared")
    first = world.row(a)["in_transition_entered_at"]
    assert first is not None and world.row(a)["last_in_transition_status"] == "PC link shared"
    world.age(a, 10)
    aged = world.row(a)["in_transition_entered_at"]
    other = world.log("Open")
    world.set_status(other, "PC link shared")                    # another call, same client, same status
    world.set_status(a, "PC link shared")                        # same value saved again
    with world.Session() as db, db.begin():
        db.execute(text("UPDATE call_analysis_results SET fd_m_date_entry = current_date WHERE id = :i"), {"i": a})   # ordinary edit
    assert world.row(a)["in_transition_entered_at"] == aged
    world.set_status(a, "PC Follow Up")                          # moving between In-Transition statuses keeps the timer
    assert world.row(a)["in_transition_entered_at"] == aged and world.row(a)["last_in_transition_status"] == "PC Follow Up"


def test_leaving_and_reentering_in_transition_restarts_the_timer(world):
    a = world.log("PC link shared")
    world.age(a, 20)
    world.set_status(a, "Open")
    assert world.row(a)["in_transition_entered_at"] is None
    world.set_status(a, "PC Follow Up")
    assert (datetime.now(timezone.utc) - world.row(a)["in_transition_entered_at"]) < timedelta(minutes=1)


@pytest.mark.parametrize("terminal,stage", [("Success PC", "success"), ("Dropped", "dropped"), ("PC link shared & Lost", "lost")])
def test_a_blank_status_never_erases_an_established_outcome(world, terminal, stage):
    a = world.log("Open")
    world.set_status(a, terminal)
    world.set_status(a, None)
    world.set_status(a, "  ")
    assert world.row(a)["case_lifecycle_stage"] == stage


def test_a_blank_status_on_an_open_call_stays_open(world):
    a = world.log("Blocker")
    world.set_status(a, None)
    assert world.row(a)["case_lifecycle_stage"] == "open"


def test_history_records_each_stage_change(world):
    a = world.log("Open")
    world.set_status(a, "PC link shared")
    world.set_status(a, "PC Follow Up")      # same stage: no event
    world.set_status(a, "Success GE")
    with world.Session() as db:
        events = db.execute(text("SELECT from_stage, to_stage FROM fd_case_lifecycle_events WHERE analysis_id = :i ORDER BY id"), {"i": a}).all()
    assert events == [("open", "in_transition"), ("in_transition", "success")]


# ---- Lost processing
def _eligible(world, status="PC link shared", days=40):
    a = world.log(status)
    world.age(a, days)
    return a


def test_eligible_calls_become_lost_with_the_status_suffix_and_the_original_preserved(world):
    ids = [_eligible(world, s) for s in lc.IN_TRANSITION_STATUSES]
    result = lost.process_lost(world.Session)
    assert result["moved"] == 5
    for aid, original in zip(ids, lc.IN_TRANSITION_STATUSES):
        row = world.row(aid)
        assert row["fd_m_case_status_change"] == original + " & Lost"
        assert row["case_lifecycle_stage"] == "lost" and row["last_in_transition_status"] == original
        assert row["lost_at"] is not None
    assert world.counts() == {"lost": 5}


def test_calls_inside_the_period_are_untouched(world):
    young = _eligible(world, days=30)
    assert lost.process_lost(world.Session)["moved"] == 0
    assert world.row(young)["case_lifecycle_stage"] == "in_transition"


def test_exact_boundary_example(world):
    a = world.log("PC link shared")
    with world.Session() as db, db.begin():      # entered 20 Oct 2026, 15:00 UTC
        db.execute(text("UPDATE call_analysis_results SET in_transition_entered_at = '2026-10-20T15:00:00Z' WHERE id = :i"), {"i": a})
    assert lost.process_lost(world.Session, today=date(2026, 11, 19))["moved"] == 0
    assert lost.process_lost(world.Session, today=date(2026, 11, 20))["moved"] == 0
    assert lost.process_lost(world.Session, today=date(2026, 11, 21))["moved"] == 1
    assert world.row(a)["case_lifecycle_stage"] == "lost"


def test_dropped_success_and_open_calls_never_become_lost(world):
    ids = [world.log(s) for s in ("Dropped", "Client Side Delay", "Success PC", "Success GE", "Open", "Blocker")]
    with world.Session() as db, db.begin():
        db.execute(text("UPDATE call_analysis_results SET in_transition_entered_at = now() - interval '90 days' WHERE id = ANY(:i)"), {"i": ids})
    assert lost.process_lost(world.Session)["moved"] == 0
    assert set(world.counts()) == {"dropped", "success", "open"}


def test_a_newer_committed_outcome_is_respected(world):
    a = _eligible(world)
    world.set_status(a, "Success PC")                # the agent closed it before the job ran
    assert lost.process_lost(world.Session)["moved"] == 0
    assert world.row(a)["fd_m_case_status_change"] == "Success PC"


def test_a_stale_job_cannot_overwrite_a_status_saved_while_it_was_running(world):
    a = _eligible(world)
    editor = world.Session()
    editor.execute(text("UPDATE call_analysis_results SET fd_m_case_status_change = 'Dropped' WHERE id = :i"), {"i": a})   # uncommitted
    outcome = {}
    worker = threading.Thread(target=lambda: outcome.update(lost.process_lost(world.Session)))
    worker.start()
    worker.join(timeout=3)
    assert worker.is_alive() is False and outcome["moved"] == 0      # SKIP LOCKED: the locked row is left alone
    editor.commit()
    editor.close()
    assert lost.process_lost(world.Session)["moved"] == 0
    assert world.row(a)["fd_m_case_status_change"] == "Dropped"


def test_repeated_runs_do_not_duplicate_anything(world):
    a = _eligible(world)
    for _ in range(3):
        lost.process_lost(world.Session)
    assert world.row(a)["fd_m_case_status_change"] == "PC link shared & Lost"        # suffix once
    assert world.scalar("SELECT count(*) FROM fd_case_lost_transitions") == 1
    assert world.scalar("SELECT count(*) FROM fd_case_status_history WHERE analysis_id = :i", i=a) == 1


def test_concurrent_workers_move_each_call_once(world):
    ids = [_eligible(world) for _ in range(30)]
    results = []
    gate = threading.Barrier(4)

    def work():
        gate.wait()
        results.append(lost.process_lost(world.Session, chunk=5)["moved"])

    threads = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(results) == 30
    assert world.scalar("SELECT count(*) FROM fd_case_lost_transitions") == 30
    assert world.scalar("SELECT count(DISTINCT analysis_id) FROM fd_case_lost_transitions") == 30
    assert all(world.row(i)["fd_m_case_status_change"] == "PC link shared & Lost" for i in ids)


def test_an_existing_lost_call_that_returns_to_in_transition_can_be_lost_again_once(world):
    a = _eligible(world)
    lost.process_lost(world.Session)
    world.set_status(a, "PC Follow Up")                    # an agent legitimately reopens it
    assert world.row(a)["case_lifecycle_stage"] == "in_transition"
    world.age(a, 40)
    lost.process_lost(world.Session)
    assert world.row(a)["fd_m_case_status_change"] == "PC Follow Up & Lost"
    assert world.scalar("SELECT count(*) FROM fd_case_lost_transitions WHERE analysis_id = :i", i=a) == 2


# ---- Teams batches
def _lose(world, n):
    for _ in range(n):
        _eligible(world)
    return lost.process_lost(world.Session)


def test_no_batch_before_ten_transitions(world):
    assert _lose(world, 9)["batches"] == 0
    assert world.scalar("SELECT count(*) FROM fd_case_lost_batches") == 0


def test_a_batch_at_ten_and_another_at_twenty(world):
    assert _lose(world, 10)["batches"] == 1
    assert _lose(world, 9)["batches"] == 0
    assert _lose(world, 1)["batches"] == 1
    assert world.scalar("SELECT count(*) FROM fd_case_lost_batches") == 2
    assert world.scalar("SELECT count(*) FROM fd_case_lost_transitions WHERE batch_id IS NULL") == 0


def test_a_batch_carries_exactly_its_ten_calls_once(world):
    ids = [_eligible(world) for _ in range(10)]
    lost.process_lost(world.Session)
    with world.Session() as db:
        payload = db.execute(text("SELECT payload FROM fd_case_lost_batches")).scalar_one()
        call_ids = {r[0] for r in db.execute(text("SELECT call_id FROM call_analysis_results WHERE id = ANY(:i)"), {"i": ids})}
    assert payload["count"] == 10 and {c["call_id"] for c in payload["calls"]} == call_ids and len(payload["calls"]) == 10


def test_cutting_batches_again_or_restarting_never_duplicates(world):
    _lose(world, 10)
    for _ in range(3):
        assert lost.cut_batches(world.Session) == 0
        lost.process_lost(world.Session)
    assert world.scalar("SELECT count(*) FROM fd_case_lost_batches") == 1


def test_concurrent_batch_cutters_create_one_batch(world):
    for _ in range(10):
        _eligible(world)
    # move the calls to Lost without cutting, then race the cutters
    with world.Session() as db, db.begin():
        rows = db.execute(lost._PICK_AND_MARK, {"cutoff": lost._cutoff(None), "chunk": 100}).mappings().all()
        for row in rows:
            db.execute(lost._RECORD, {"analysis_id": row["id"], "call_id": row["call_id"], "entered": row["entered"],
                                       "old_status": row["old_status"], "new_status": row["new_status"]})
    gate = threading.Barrier(4)
    created = []

    def cut():
        gate.wait()
        created.append(lost.cut_batches(world.Session))

    threads = [threading.Thread(target=cut) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(created) == 1 and world.scalar("SELECT count(*) FROM fd_case_lost_batches") == 1


class _Response:
    def __init__(self, code):
        self.status_code = code


def test_delivery_retries_with_backoff_and_never_sends_a_delivered_batch_again(world, monkeypatch):
    monkeypatch.setenv("LOST_CASE_TEAMS_WEBHOOK_URL", "https://example.invalid/hook")
    _lose(world, 10)
    calls = []

    def flaky(url, json=None, headers=None, timeout=None):
        calls.append(headers["Idempotency-Key"])
        return _Response(500 if len(calls) == 1 else 200)

    now = datetime.now(timezone.utc)
    assert lost.deliver_pending(world.Session, post=flaky, now=now) == {"sent": 0, "failed": 1}
    assert lost.deliver_pending(world.Session, post=flaky, now=now) == {"sent": 0, "failed": 0}          # backing off
    assert lost.deliver_pending(world.Session, post=flaky, now=now + timedelta(minutes=5)) == {"sent": 1, "failed": 0}
    assert lost.deliver_pending(world.Session, post=flaky, now=now + timedelta(days=1)) == {"sent": 0, "failed": 0}
    assert len(calls) == 2 and len(set(calls)) == 1                      # same idempotency key on the retry
    assert world.scalar("SELECT delivered_at IS NOT NULL FROM fd_case_lost_batches")


def test_without_a_webhook_batches_stay_pending(world, monkeypatch):
    monkeypatch.delenv("LOST_CASE_TEAMS_WEBHOOK_URL", raising=False)
    _lose(world, 10)
    assert lost.deliver_pending(world.Session)["disabled"] is True
    assert world.scalar("SELECT count(*) FROM fd_case_lost_batches WHERE delivered_at IS NULL") == 1


def test_backfill_categorises_existing_rows_without_deduplicating(world):
    with world.Session() as db, db.begin():
        db.execute(text("ALTER TABLE call_analysis_results DISABLE TRIGGER fd_case_lifecycle"))
        for status in ["Open", "Open", "PC link shared", "Success PC", None, "PC shared and lost", "Move to next date"]:
            db.execute(text("INSERT INTO call_analysis_results (call_id, fd_m_case_status_change) VALUES (:c, :s)"), {"c": str(uuid.uuid4()), "s": status})
        db.execute(text("ALTER TABLE call_analysis_results ENABLE TRIGGER fd_case_lifecycle"))
    assert world.counts() == {None: 7}
    from ops import backfill_case_lifecycle as backfill
    for _ in range(2):                                            # rerun-safe
        with world.Session() as db, db.begin():
            db.execute(text(backfill.BACKFILL), {"batch": 100})
    assert world.counts() == {"open": 4, "in_transition": 1, "success": 1, "lost": 1}
    assert sum(world.counts().values()) == 7
    with world.Session() as db:
        entered = db.execute(text("SELECT in_transition_entered_at FROM call_analysis_results WHERE case_lifecycle_stage = 'in_transition'")).scalar()
    assert (datetime.now(timezone.utc) - entered) < timedelta(minutes=1)      # no reliable history: timer starts at migration


def test_backfill_uses_history_then_date_entry_never_the_call_date(world):
    a = world.log(None)
    with world.Session() as db, db.begin():
        db.execute(text("ALTER TABLE call_analysis_results DISABLE TRIGGER fd_case_lifecycle"))
        db.execute(text("UPDATE call_analysis_results SET fd_m_case_status_change = 'PC link shared', case_lifecycle_stage = NULL WHERE id = :i"), {"i": a})
        db.execute(text("ALTER TABLE call_analysis_results ENABLE TRIGGER fd_case_lifecycle"))
        db.execute(text("INSERT INTO fd_case_status_history (analysis_id, call_id, previous_status, new_status, changed_at)"
                        " VALUES (:i, 'x', 'Open', 'PC link shared', '2026-09-01T12:00:00Z')"), {"i": a})
    from ops import backfill_case_lifecycle as backfill
    with world.Session() as db, db.begin():
        db.execute(text(backfill.BACKFILL), {"batch": 100})
    assert world.row(a)["in_transition_entered_at"] == datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("new,legacy", [("Success PC", "Success - PC"), ("Success GE", "Success - GE"),
                                        ("Initial Email Follow Up", "initial email followup"),
                                        ("Initial email sent (Fee Details shared)", "Initial email sent."),
                                        ("PC Follow Up & Lost", "PC Follow Up & Lost"), ("Fee details shared & FU", "Fee details shared & FU"), ("Open", "Open"), ("Dropped", "Dropped")])
def test_dashboard_calculator_receives_the_spelling_its_formulas_expect(new, legacy):
    assert lc.metric_status(new) == legacy


def test_for_metric_calculator_does_not_modify_its_input():
    rows = [{"fd_m_case_status_change": "Success PC"}, {"fd_m_case_status_change": None}]
    assert [r["fd_m_case_status_change"] for r in lc.for_metric_calculator(rows)] == ["Success - PC", None]
    assert rows[0]["fd_m_case_status_change"] == "Success PC"


def test_accepted_status_rules():
    assert lc.accepted_status("success pc") == "Success PC"
    assert lc.accepted_status("Success - GE") == "Success GE"             # stale tab
    assert lc.accepted_status("PC link shared & Lost", "PC link shared & Lost") == "PC link shared & Lost"   # unchanged re-save
    assert lc.accepted_status("PC link shared & Lost", "Open") is None                                       # cannot be typed in
    assert lc.accepted_status("Bogus") is None
