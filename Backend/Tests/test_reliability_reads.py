"""Semantic and payload-boundary tests against a disposable LOCAL PostgreSQL."""
import os
from datetime import date, datetime, time, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from storage.db import Base
from storage.models import Call, CallAnalysisResult, Client, FDLead, FDAssignment, FDUser, FDSyncFilter, FDTask, Dependency
from services.analysis_read_query import analysis_read
from services.lead_read_query import filtered_leads
from routes.flintdesk.dashboard_metrics import get_time_duration


@pytest.fixture
def db():
    url = os.getenv("RELIABILITY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set RELIABILITY_TEST_DATABASE_URL to a disposable local PostgreSQL")
    assert make_url(url).host in {"127.0.0.1", "localhost"}
    schema = "reliability_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema},public"})
    try:
        Base.metadata.create_all(engine, tables=[model.__table__ for model in
            (Client, Call, CallAnalysisResult, FDLead, FDAssignment, FDUser, FDSyncFilter, FDTask)])
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE fd_dependencies (id UUID PRIMARY KEY, client_enquiry_date DATE, status TEXT)"))
            # Match production's UUID column despite its legacy ORM Text type.
            connection.execute(text("ALTER TABLE call_analysis_results ALTER COLUMN call_id TYPE uuid USING call_id::uuid"))
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def add_call(db, *, day=date(2026, 9, 22), number=1, billsec=10, voicemail=False, **kwargs):
    call = Call(call_id=uuid4(), call_date=day, call_time=time(10, 0, number),
                src="+1 (312) 878-1255", dst="3125550199", extension="101",
                call_number=number, billsec=billsec, transcript="large private transcript", **kwargs)
    db.add(call)
    db.flush()
    analysis = CallAnalysisResult(call_id=str(call.call_id), call_number=number,
        call_recording_date_normalized=day, call_recording_date=str(day),
        voicemail_left_if_no_answer="yes" if voicemail else "no",
        raw_llm_json={"private": "large JSON"})
    db.add(analysis)
    db.flush()
    return call, analysis


def test_analysis_filters_before_pagination_and_never_loads_heavy_fields(db):
    for number in range(1, 5):
        add_call(db, number=number, voicemail=number != 1)
    db.expunge_all()
    queries = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)
    event.listen(db.bind, "before_cursor_execute", capture)
    rows, calls, total = analysis_read(db, start=date(2026, 9, 22), end=date(2026, 9, 22),
                                      call_kind="voicemail", page=2, page_size=2)
    assert total == 3 and len(rows) == 1
    assert "raw_llm_json" not in rows[0].__dict__
    assert "transcript" not in next(iter(calls.values())).__dict__
    assert any("LIMIT" in sql and "row_number()" in sql for sql in queries)
    assert all("calls.transcript" not in sql and "call_analysis_results.raw_llm_json" not in sql for sql in queries)


def test_legacy_dates_and_invalid_dates(db):
    _, valid = add_call(db, number=1)
    _, invalid = add_call(db, number=2)
    valid.call_recording_date_normalized = None
    valid.call_recording_date = "9/22/2026"
    invalid.call_recording_date_normalized = None
    invalid.call_recording_date = "02/30/2026"
    db.flush()
    rows, _, total = analysis_read(db, start=date(2026, 9, 22), end=date(2026, 9, 22), page=1, page_size=10)
    assert total == 1 and rows[0].id == valid.id


@pytest.mark.parametrize("call_source", [False, True])
def test_keyset_pages_are_bounded_signed_and_filter_scoped(db, call_source):
    from fastapi import HTTPException
    for number in range(1, 6):
        add_call(db, number=number)
    kwargs = dict(start=date(2026, 9, 22), end=date(2026, 9, 22),
                  page=1, page_size=2, cursor_mode=True, call_source=call_source)
    metadata = {}
    first, _, total = analysis_read(db, **kwargs, cursor_output=metadata)
    assert total == 5 and len(first) == 2 and metadata["next_cursor"]
    token = metadata["next_cursor"]
    second, _, _ = analysis_read(db, **kwargs, cursor=token, cursor_output=metadata)
    identity = lambda row: str(row.call_id) if call_source else row.id
    assert not set(map(identity, first)) & set(map(identity, second))
    third, _, _ = analysis_read(db, **kwargs, cursor=metadata["next_cursor"], cursor_output=metadata)
    assert len(third) == 1 and metadata["next_cursor"] is None
    with pytest.raises(HTTPException):
        analysis_read(db, **kwargs, cursor=token, client_number="3125550199")
    with pytest.raises(HTTPException):
        analysis_read(db, **kwargs, cursor="invalid")


def test_call_source_keeps_unanalysed_calls_and_sql_agent_filter(db):
    owner = FDUser(id=uuid4(), name="Alex", email="alex@example.test", pass_hash="test")
    db.add(owner)
    call, analysis = add_call(db)
    analysis.front_desk_person_name = "Alex"
    db.flush()
    rows, _, total = analysis_read(db, start=call.call_date, end=call.call_date,
                                   call_source=True, agent="Alex", page=1, page_size=2)
    assert total == 1 and rows[0].call_id == call.call_id
    db.delete(analysis)
    db.flush()
    rows, _, total = analysis_read(db, start=call.call_date, end=call.call_date,
                                   call_source=True, page=1, page_size=2)
    assert total == 1 and rows[0].call_id == call.call_id


def test_duration_sql_handles_voicemail_negative_null_and_zero(db):
    add_call(db, number=1, billsec=20)
    add_call(db, number=2, billsec=30, voicemail=True)
    add_call(db, number=3, billsec=-4)
    add_call(db, number=4, billsec=None)
    add_call(db, number=5, billsec=0)
    assert get_time_duration(date(2026, 9, 22), date(2026, 9, 22), None, {}, db) == {
        "totalCallDurationSeconds": 20, "voicemailCallDurationSeconds": 30, "qualifyingCallCount": 2}


def test_dashboard_summary_returns_aggregates_and_daily_target(db):
    from services.dashboard_summary import build_summary
    owner = FDUser(id=uuid4(), name="Alex", email="alex@example.test", pass_hash="test", role="agent")
    db.add(owner)
    _, analysis = add_call(db)
    analysis.front_desk_person_name = "Alex"
    analysis.fd_a_call_summary = "Documented normal call"
    db.commit()
    summary = build_summary(db, {"role": "agent", "fd_user_id": str(owner.id)}, date(2026, 9, 22), date(2026, 9, 22))
    assert summary["dailyCallTargetRows"] == [{"agent": "Alex", "date": "2026-09-22", "callsMade": 1, "target": 35, "difference": -34}]
    assert summary["callKindCounts"]["total"] == 1
    assert summary["timeDurations"]["totalCallDurationSeconds"] == 10
    assert "calls" not in summary
    import json
    assert "large private transcript" not in json.dumps(summary)


def test_lead_filter_uses_central_day_before_limit(db):
    for number, hour in enumerate((3, 7, 8), 1):
        db.add(FDLead(id=uuid4(), fd_m_leads_lead_id=f"L{number}",
                      lead_added_at=datetime(2026, 9, 22, hour, tzinfo=timezone.utc)))
    db.flush()
    result = filtered_leads(db, start_date=date(2026, 9, 22), end_date=date(2026, 9, 22), page=2, page_size=1).all()
    assert [row.fd_m_leads_lead_id for row in result] == ["L2"]


def test_lead_owner_scope_and_literal_search(db):
    owner_id = uuid4()
    # An unresolved owner id cannot match a different persisted owner by name.
    from types import SimpleNamespace
    db.add(FDLead(id=uuid4(), fd_m_leads_lead_id="100%", fd_m_leads_owner="Alex",
                 lead_added_at=datetime.now(timezone.utc)))
    db.add(FDLead(id=uuid4(), fd_m_leads_lead_id="100x", fd_m_leads_owner="Other",
                 lead_added_at=datetime.now(timezone.utc)))
    db.flush()
    rows = filtered_leads(db, owner=SimpleNamespace(id=owner_id, name="Alex"), lead_id="%").all()
    assert [row.fd_m_leads_lead_id for row in rows] == ["100%"]


def _two_owners_with_history(db):
    first = FDUser(id=uuid4(), name="First Agent", email="first@example.test", pass_hash="test")
    second = FDUser(id=uuid4(), name="Second Agent", email="second@example.test", pass_hash="test")
    db.add_all([first, second])
    db.flush()
    for day, user in ((date(2026, 9, 10), first), (date(2026, 9, 23), second)):
        db.add(FDAssignment(id=uuid4(), assignment_date=day, extension="101", number="3128781255",
                            agent_id=user.id, created_at=datetime.now(timezone.utc)))
    return first, second


def _agent_total(db, name, day=date(2026, 9, 22)):
    return analysis_read(db, start=day, end=day, agent=name, page=1, page_size=10)[2]


def test_assignment_history_decides_the_agent_by_the_calls_date(db):
    first, second = _two_owners_with_history(db)
    add_call(db, number=1)                                           # 22 Sep: before the 23 Sep hand-over -> First Agent
    add_call(db, day=date(2026, 9, 24), number=2)                    # 24 Sep: after it -> Second Agent
    db.flush()
    assert get_time_duration(date(2026, 9, 22), date(2026, 9, 22), [first.id], {}, db)["totalCallDurationSeconds"] == 10
    assert get_time_duration(date(2026, 9, 22), date(2026, 9, 22), [second.id], {}, db)["qualifyingCallCount"] == 0
    assert _agent_total(db, "First Agent") == 1 and _agent_total(db, "Second Agent") == 0
    assert _agent_total(db, "Second Agent", date(2026, 9, 24)) == 1 and _agent_total(db, "First Agent", date(2026, 9, 24)) == 0


def test_the_assigned_extension_owner_wins_over_a_typed_agent_name(db):
    """Rule since commit a0136c7 (deployed): "assigned extension owner wins over typed names". This test used to expect the
    typed name to override the assignment; that was the rule before and the test was never updated."""
    _two_owners_with_history(db)
    _, analysis = add_call(db, number=1)
    analysis.front_desk_person_name = "Second Agent"                 # typed on a call that First Agent owns by assignment
    db.flush()
    assert _agent_total(db, "First Agent") == 1                      # the owner keeps the call
    assert _agent_total(db, "Second Agent") == 0                     # the typed name does not take it away


def test_a_typed_agent_name_claims_a_call_nobody_is_assigned_to(db):
    _two_owners_with_history(db)
    call, analysis = add_call(db, number=1)
    call.extension = "999"                                           # an extension with no assignment at all
    analysis.front_desk_person_name = "Second Agent"
    db.flush()
    assert _agent_total(db, "Second Agent") == 1
    assert _agent_total(db, "First Agent") == 0


def test_canonical_duplicate_prefers_manual_data(db):
    first, _ = add_call(db, number=1, billsec=30)
    second, analysis = add_call(db, number=2, billsec=20)
    second.call_time = first.call_time
    analysis.front_desk_person_name = "Manual owner"
    db.flush()
    rows, _, total = analysis_read(db, start=date(2026, 9, 22), end=date(2026, 9, 22), page=1, page_size=10)
    assert total == 1 and rows[0].id == analysis.id


def test_lead_filter_matches_proposed_call_before_pagination(db):
    call, _ = add_call(db)
    db.add(FDLead(id=uuid4(), fd_m_leads_lead_id="proposed", fd_m_leads_phone="3125550199",
                  lead_added_at=datetime(2026, 9, 22, 16, tzinfo=timezone.utc)))
    db.flush()
    rows = filtered_leads(db, call_id=str(call.call_id), extension="101", page=1, page_size=1).all()
    assert len(rows) == 1 and rows[0].fd_m_leads_lead_id == "proposed"


def test_list_serializers_do_not_lazy_load_heavy_content(db):
    from routes.calls import serialize_analysis_rows, get_call_content
    from routes.flintdesk.leads import _lead_rows
    call, analysis = add_call(db)
    call_id = call.call_id
    db.add(FDLead(id=uuid4(), fd_m_leads_lead_id="linked", fd_a_leads_call_id=call_id,
                  lead_added_at=datetime(2026, 9, 22, 16, tzinfo=timezone.utc)))
    db.flush()
    db.expunge_all()
    rows, calls, _ = analysis_read(db, start=date(2026, 9, 22), end=date(2026, 9, 22), page=1, page_size=1)
    result = serialize_analysis_rows(rows, db, include_raw=False, calls_by_id=calls, include_transcript=False)
    assert result[0]["transcript"] is None and "raw_llm_json" not in result[0]
    leads = _lead_rows(db, None, selected_rows=filtered_leads(db, page=1, page_size=1).all())
    assert leads[0]["call_details"]["transcript"] == ""
    content = get_call_content(call_id, True, db, {})
    assert content["transcript"] == "large private transcript" and content["raw_llm_json"] == {"private": "large JSON"}


def test_release_migration_is_once_and_failure_rolls_back(db, monkeypatch):
    from types import ModuleType
    import sys
    from migrations.runner import run_migrations
    module = ModuleType("test_migration_" + uuid4().hex)
    module.engine = db.bind
    def migrate():
        with module.engine.begin() as conn:
            conn.execute(text("CREATE TABLE migration_probe (value int)"))
            conn.execute(text("INSERT INTO migration_probe VALUES (1)"))
    module.migrate = migrate
    monkeypatch.setitem(sys.modules, module.__name__, module)
    manifest = ((module.__name__, "migrate"),)
    run_migrations(db.bind, manifest=manifest)
    run_migrations(db.bind, manifest=manifest)
    assert db.execute(text("SELECT count(*) FROM migration_probe")).scalar() == 1
    assert module.engine is db.bind
    db.rollback()

    broken = ModuleType("test_migration_" + uuid4().hex)
    broken.engine = db.bind
    def fail():
        with broken.engine.begin() as conn:
            conn.execute(text("INSERT INTO migration_probe VALUES (2)"))
            raise RuntimeError("failed release")
    broken.migrate = fail
    monkeypatch.setitem(sys.modules, broken.__name__, broken)
    with pytest.raises(RuntimeError, match="failed release"):
        run_migrations(db.bind, manifest=((broken.__name__, "migrate"),))
    assert db.execute(text("SELECT count(*) FROM migration_probe")).scalar() == 1
    assert db.execute(text("SELECT count(*) FROM public.app_schema_migrations WHERE version=:v"), {"v": broken.__name__}).scalar() == 0


def test_release_migration_lock_wait_is_bounded(db, monkeypatch):
    import sys
    import time as clock
    from types import ModuleType
    from sqlalchemy.exc import OperationalError
    from migrations.runner import run_migrations
    with db.bind.begin() as conn:
        conn.execute(text("CREATE TABLE lock_probe (value int)"))
    module = ModuleType("test_migration_" + uuid4().hex)
    module.engine = db.bind
    def migrate():
        with module.engine.begin() as conn:
            conn.execute(text("ALTER TABLE lock_probe ADD COLUMN other int"))
    module.migrate = migrate
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with db.bind.connect() as blocker, blocker.begin():
        blocker.execute(text("SELECT * FROM lock_probe"))
        started = clock.monotonic()
        with pytest.raises(OperationalError) as error:
            run_migrations(db.bind, manifest=((module.__name__, "migrate"),))
        assert error.value.orig.pgcode == "55P03"
        assert clock.monotonic() - started < 5


def test_leads_list_newest_entry_date_first_then_highest_lead_number(db):
    """Today's leads on top; within a day the higher LD number first; the order holds across pages and for S1 and S2 alike."""
    rows = [("LD#1001", "2026-09-20"), ("LD#1010", "2026-09-22"), ("LD#1005", "2026-09-22"), ("LD#2000", "2026-09-21"),
            ("LD#999", "2026-09-22"), ("LD#1002", "2026-09-20"), ("MANUAL", "2026-09-22")]
    for index, (lead_id, received) in enumerate(rows):
        db.add(FDLead(id=uuid4(), fd_m_leads_lead_id=lead_id, fd_m_leads_date_received=received,
                      lead_added_at=datetime(2026, 9, 22, 12, index, tzinfo=timezone.utc)))
    db.flush()
    expected = ["LD#1010", "LD#1005", "LD#999", "MANUAL", "LD#2000", "LD#1002", "LD#1001"]
    assert [row.fd_m_leads_lead_id for row in filtered_leads(db).all()] == expected
    paged = [row.fd_m_leads_lead_id for number in (1, 2, 3, 4) for row in filtered_leads(db, page=number, page_size=2).all()]
    assert paged == expected                                                   # no row lost or repeated between pages
    only_20th = filtered_leads(db, start_date=date(2026, 9, 20), end_date=date(2026, 9, 20)).all()
    assert [row.fd_m_leads_lead_id for row in only_20th] == ["LD#1002", "LD#1001"]


def test_a_lead_without_a_valid_received_date_is_listed_under_its_import_day(db):
    db.add(FDLead(id=uuid4(), fd_m_leads_lead_id="LD#10", fd_m_leads_date_received="not a date", lead_added_at=datetime(2026, 9, 23, 15, tzinfo=timezone.utc)))
    db.add(FDLead(id=uuid4(), fd_m_leads_lead_id="LD#500", fd_m_leads_date_received="2026-09-22", lead_added_at=datetime(2026, 9, 22, 15, tzinfo=timezone.utc)))
    db.flush()
    assert [row.fd_m_leads_lead_id for row in filtered_leads(db).all()] == ["LD#10", "LD#500"]


def test_lead_call_id_filter_matches_the_call_reference_shown_in_the_table(db):
    from uuid import uuid4 as _u
    call_id = _u()
    db.add(Call(call_id=call_id, call_date=date(2026, 10, 22), call_reference="AF#13978", call_time=time(10, 0),
                src="3125550177", dst="101"))
    db.add(FDLead(id=uuid4(), fd_m_leads_lead_id="LD#1", fd_a_leads_call_id=call_id,
                  lead_added_at=datetime(2026, 10, 22, 16, tzinfo=timezone.utc)))
    db.add(FDLead(id=uuid4(), fd_m_leads_lead_id="LD#2",
                  lead_added_at=datetime(2026, 10, 22, 16, tzinfo=timezone.utc)))
    db.flush()
    for needle in ("AF#13978", "af#13978", " 13978"):
        assert [r.fd_m_leads_lead_id for r in filtered_leads(db, call_id=needle).all()] == ["LD#1"]
    assert [r.fd_m_leads_lead_id for r in filtered_leads(db, call_id=str(call_id)[:8]).all()] == ["LD#1"]
    assert filtered_leads(db, call_id="AF#99999").all() == []


def _lead_filter_fixture(db):
    """Three leads on 22 Oct with distinct owner, phone, extension and call."""
    day = date(2026, 10, 22)
    calls = {}
    for client, phone in (("CL-A", "3125550101"), ("CL-B", "3125550202"), ("CL-C", "9998887777")):
        db.add(Client(client_id=client, phone_number=phone, first_call_date=day, last_call_date=day,
                      created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
    db.flush()
    for key, src, ext, client, ref in (
        ("a", "3125550101", "101", "CL-A", "AF#100"),
        ("b", "3125550202", "202", "CL-B", "AF#200"),
        ("c", "9998887777", "303", "CL-C", "AF#300"),
    ):
        calls[key] = uuid4()
        db.add(Call(call_id=calls[key], call_date=day, call_time=time(10, 0), src=src, dst="8005550000",
                    extension=ext, client_id=client, call_reference=ref))
    for key, lid, phone, owner, ext, d in (
        ("a", "LD#1", "(312) 555-0101", "Agent One", "101", "2026-10-22"),
        ("b", "LD#2", "312-555-0202", "Agent Two", "202", "2026-10-22"),
        ("c", "LD#3", "999 888 7777", "Agent One", "", "2026-10-21"),
    ):
        db.add(FDLead(id=uuid4(), fd_m_leads_lead_id=lid, fd_m_leads_phone=phone, fd_m_leads_owner=owner,
                      fd_m_leads_extension=ext, fd_m_leads_date_received=d, fd_a_leads_call_id=calls[key],
                      lead_added_at=datetime(2026, 10, 22, 16, tzinfo=timezone.utc)))
    db.add(FDLead(id=uuid4(), fd_m_leads_lead_id="LD#4", fd_m_leads_date_received="2026-10-20",
                  lead_added_at=datetime(2026, 10, 20, 16, tzinfo=timezone.utc)))
    first = FDUser(id=uuid4(), name="Agent One", email="one@example.test", pass_hash="x")
    db.add(first)
    db.flush()
    db.add(FDAssignment(id=uuid4(), assignment_date=day, extension="303", number="8005550000",
                        agent_id=first.id, created_at=datetime.now(timezone.utc)))
    db.flush()


def _ids(db, **kwargs):
    return sorted(r.fd_m_leads_lead_id for r in filtered_leads(db, **kwargs).all())


def test_every_lead_filter_returns_the_right_rows(db):
    _lead_filter_fixture(db)
    assert _ids(db) == ["LD#1", "LD#2", "LD#3", "LD#4"]
    assert _ids(db, start_date=date(2026, 10, 22), end_date=date(2026, 10, 22)) == ["LD#1", "LD#2"]
    assert _ids(db, start_date=date(2026, 10, 21), end_date=date(2026, 10, 22)) == ["LD#1", "LD#2", "LD#3"]
    assert _ids(db, end_date=date(2026, 10, 20)) == ["LD#4"]
    assert _ids(db, lead_id="ld#2") == ["LD#2"]
    assert _ids(db, client_number="312-555-0101") == ["LD#1"]
    assert _ids(db, client_number="+1 (999) 888-7777") == ["LD#3"]
    assert _ids(db, agent="agent one") == ["LD#1", "LD#3"]
    assert _ids(db, agent="all") == ["LD#1", "LD#2", "LD#3", "LD#4"]
    assert _ids(db, extension="202") == ["LD#2"]
    assert _ids(db, extension="101") == ["LD#1"]
    assert _ids(db, client_id="cl-b") == ["LD#2"]
    assert _ids(db, call_id="AF#300") == ["LD#3"]
    assert _ids(db, call_id="af#1") == ["LD#1"]
    assert _ids(db, lead_number="800-555-0000") == ["LD#1", "LD#2", "LD#3"]
    assert _ids(db, lead_number="3125550202") == ["LD#2"]
    # Filters combine (AND), and a non-matching combination is empty.
    assert _ids(db, agent="Agent One", extension="101") == ["LD#1"]
    assert _ids(db, agent="Agent Two", extension="101") == []
    assert _ids(db, client_id="CL-A", call_id="AF#200") == []
