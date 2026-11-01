"""Contract tests of the closing agent's own-related-logs flow through the REAL routes.

Real FastAPI routers (Calls field save, manual-fields, case closures, Calls list), the real ORM schema and the real
case_closure service on a DISPOSABLE local PostgreSQL. Only authentication is replaced (the signed-in user is a
test account), except one test that keeps the real dependency to prove the routes reject anonymous callers.

    CASE_CLOSURE_TEST_DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:55433/contract_test \
    DATABASE_URL=<same> SECRET_KEY=test-only pytest Tests/test_own_related_contract.py
"""
import os
import re
import sys
import threading
import uuid
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

URL = os.getenv("CASE_CLOSURE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs a disposable local PostgreSQL (CASE_CLOSURE_TEST_DATABASE_URL)")

CLIENT_PHONE = "3125550100"
SYSTEM_NUMBER = "8005550000"
EXT = {"Priyanka": "101", "Ravi": "102", "Meena": "103"}


@pytest.fixture
def env(monkeypatch):
    assert make_url(URL).host in {"localhost", "127.0.0.1"}, "tests require a local database"
    from middleware.auth import get_current_user
    from routes import calls as calls_routes
    from routes.flintdesk import calls as fd_calls, case_closures
    from storage.db import Base, get_db
    from storage import models

    schema = "t_" + uuid.uuid4().hex[:12]
    admin = create_engine(URL)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(URL, connect_args={"options": f"-c search_path={schema}"})
    # only the tables these routes touch (a few other ORM tables use server defaults that need PostgreSQL extensions)
    needed = {"clients", "calls", "call_analysis_results", "fd_users", "fd_sync_filters", "fd_assignments", "task_notifications",
              "tasks", "fd_leads"}
    from importlib import import_module
    # The closure tables and indexes come from the real migrations 071/072 (run on this disposable schema only).
    m071 = import_module("migrations.071_create_case_closures"); m072 = import_module("migrations.072_notification_closure_columns")
    monkeypatch.setattr(m071, "engine", engine); monkeypatch.setattr(m072, "engine", engine)
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name in needed - {"task_notifications"}])
    m071.migrate_create_case_closures()
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name == "task_notifications"])
    m072.migrate_notification_closure_columns()
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE IF NOT EXISTS fd_case_status_history (
            id BIGSERIAL PRIMARY KEY, analysis_id BIGINT NOT NULL REFERENCES call_analysis_results(id) ON DELETE CASCADE,
            call_id TEXT NOT NULL, previous_status TEXT, new_status TEXT NOT NULL,
            changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), changed_by UUID REFERENCES fd_users(id) ON DELETE SET NULL)"""))
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    app = FastAPI()
    app.include_router(fd_calls.router)
    app.include_router(case_closures.router)
    app.include_router(calls_routes.router)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    who = {"user": None}
    app.dependency_overrides[get_current_user] = lambda: who["user"]

    class Env:
        pass

    e = Env()
    e.app, e.Session, e.engine, e.who, e.get_current_user = app, Session, engine, who, get_current_user
    # Older tests analyse/count calls dated before the AI start date (default 14 Oct 2026): open the window for them; the
    # AI-start-date tests below set it explicitly.
    monkeypatch.setenv("OPEN_CALL_DEPENDENCY_AI_SINCE", "2026-01-01")
    e.client = TestClient(app)
    e.users = {}
    now = datetime.now(timezone.utc)
    with Session() as db, db.begin():
        db.add(models.FDSyncFilter(id=uuid.uuid4(), filter_type="number", value=SYSTEM_NUMBER, created_at=now))
        for name in ("Priyanka", "Ravi", "Meena"):
            uid = uuid.uuid4()
            db.add(models.FDUser(id=uid, name=name, email=f"{name.lower()}@test.local", pass_hash="x", is_active=True, role="agent", created_at=now))
            e.users[name] = str(uid)
            # Each agent owns one extension on the system number (ownership and completion rights follow the assignment).
            db.add(models.FDAssignment(id=uuid.uuid4(), assignment_date=date(2026, 10, 1), extension=EXT[name], number=SYSTEM_NUMBER,
                                       agent_id=uid, created_at=now, is_active=True))
        db.add(models.Client(client_id="CL-1", phone_number=CLIENT_PHONE, client_name="Jane Roe", first_call_date=date(2026, 10, 1),
                             last_call_date=date(2026, 10, 9), created_at=now, updated_at=now))
        db.add(models.Client(client_id="CL-2", phone_number="3125559999", client_name="Other", first_call_date=date(2026, 10, 1),
                             last_call_date=date(2026, 10, 9), created_at=now, updated_at=now))
    e.n = 0

    def as_user(name):
        e.who["user"] = {"fd_user_id": e.users[name], "name": name, "role": "agent", "email": f"{name.lower()}@test.local"}
    e.as_user = as_user

    def log(agent, case_type="H-1B extension", status=None, client_id="CL-1", stage2=True, completed=False, day=date(2026, 10, 9), transcript=None, action="Call back"):
        e.n += 1
        call_id = uuid.uuid4()
        with Session() as db, db.begin():
            db.add(models.Call(call_id=call_id, client_id=client_id, src=SYSTEM_NUMBER, dst=CLIENT_PHONE, disposition="ANSWERED",
                               extension=EXT[agent], call_date=day, call_time=dtime(10, e.n % 60), call_reference=f"AF#{900 + e.n}", transcript=transcript))
            db.flush()
            row = models.CallAnalysisResult(call_id=str(call_id), call_recording_date=str(day), call_recording_date_normalized=day, call_reference=f"AF#{900 + e.n}", fd_a_case_type=case_type,
                                            fd_m_case_status_change=status, case_closing_completed=completed,
                                            created_at=now,
                                            **({"fd_m_self_action_item": action, "fd_m_manual_action_status": "Open",
                                                "fd_m_dependency": "None", "fd_m_follow_up_dates": "10-20-2026"} if stage2 else {}))
            db.add(row); db.flush()
            aid = row.id
        return {"call_id": str(call_id), "analysis_id": aid, "ref": f"AF#{900 + e.n}"}
    e.log = log

    def save_status(l, value, expected=""):
        return e.client.patch(f"/api/flintdesk/calls/{l['call_id']}/field",
                              json={"field": "fd_m_case_status_change", "value": value, "expected_value": expected})
    e.save_status = save_status

    def q(sql, **p):
        with Session() as db:
            return db.execute(text(sql), p).all()
    e.q = q
    e.status = lambda l: q("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id=:i", i=l["analysis_id"])[0][0]
    e.completed = lambda l: q("SELECT case_closing_completed FROM call_analysis_results WHERE id=:i", i=l["analysis_id"])[0][0]
    e.offer = lambda l: e.client.get("/flintdesk/case-closures/own-related", params={"call_id": l["call_id"]})
    yield e
    engine.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


def _confirm(e, offer, ids=None):
    return e.client.post(f"/flintdesk/case-closures/{offer['closure_id']}/own-related/confirm",
                         json={"analysis_ids": ids if ids is not None else [l["analysis_id"] for l in offer["logs"]]})


def test_end_to_end_close_offer_confirm_through_the_real_routes(env):
    env.as_user("Priyanka")
    a, b, c = env.log("Priyanka"), env.log("Priyanka", "H-1B visa", status="PC Follow Up"), env.log("Priyanka", status="Open")
    env.log("Priyanka", "Divorce"); env.log("Priyanka", client_id="CL-2")                  # other context / other client
    env.as_user("Ravi"); assert env.save_status(env.log("Ravi"), "Blocker").status_code == 200   # a non-closing save offers nothing
    env.as_user("Priyanka"); assert env.save_status(env.log("Ravi"), "Blocker").status_code == 403    # and nobody edits another agent's call
    r = env.save_status(a, "Success PC")
    assert r.status_code == 200 and r.json()["normalized_value"] == "Success PC"
    offer = env.offer(a).json()
    assert set(offer) >= {"closure_id", "closure_status", "client_name", "client_number", "case_context", "logs"}, offer
    assert offer["client_name"] == "Jane Roe" and offer["client_number"] == CLIENT_PHONE and offer["closure_status"] == "Success PC"
    assert sorted(l["analysis_id"] for l in offer["logs"]) == sorted([b["analysis_id"], c["analysis_id"]])
    assert all(set(l) >= {"analysis_id", "call_id", "call_reference", "call_date", "status", "case_context", "agent_name"} for l in offer["logs"])
    r = _confirm(env, offer)
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "confirmed" and body["skipped"] == [] and body["closure_status"] == "Success PC"
    assert {u["analysis_id"] for u in body["updated"]} == {b["analysis_id"], c["analysis_id"]}
    assert all(set(u) >= {"call_id", "call_reference", "previous_status", "new_status", "fd_m_date_entry"} for u in body["updated"])
    assert {env.status(b), env.status(c)} == {"Success PC"}
    assert env.offer(a).json()["logs"] == []


@pytest.mark.parametrize("status", ["Dropped", "Success GE", "Success PC"])
def test_each_closing_status_state_after_confirmation(env, status):
    """Closed (not open), status/date/history written, completion NOT set by the confirmation, Submit then completes."""
    env.as_user("Priyanka")
    a, b = env.log("Priyanka"), env.log("Priyanka", "H-1B visa", status="PC Follow Up")
    env.save_status(a, status)
    assert env.completed(a) in (False, None)                      # saving the closing call itself does not complete it either
    offer = env.offer(a).json()
    _confirm(env, offer)
    assert env.status(b) == status
    assert env.q("SELECT fd_m_date_entry FROM call_analysis_results WHERE id=:i", i=b["analysis_id"])[0][0] is not None
    assert env.q("SELECT previous_status, new_status FROM fd_case_status_history WHERE analysis_id=:i", i=b["analysis_id"]) == [("PC Follow Up", status)]
    assert env.completed(b) in (False, None)                      # same as the closing call: completion is Submit's job
    # no longer open (the Calls "Open cases" rule is status based)
    from services.analysis_read_query import OPEN_CASE_TERMINAL_STATUSES
    assert status.lower() in OPEN_CASE_TERMINAL_STATUSES
    # the established Submit (manual-fields completion save) completes it and leaves the status alone
    done = env.client.patch(f"/calls/manual-fields/{b['analysis_id']}", json={"case_closing_completed": True})
    assert done.status_code == 200, done.text
    assert env.completed(b) is True and env.status(b) == status


def test_open_calls_filter_excludes_the_closed_related_logs(env):
    env.as_user("Priyanka")
    a, b, keep = env.log("Priyanka"), env.log("Priyanka", "H-1B visa"), env.log("Priyanka", "Divorce")
    env.save_status(a, "Dropped"); _confirm(env, env.offer(a).json())
    r = env.client.get("/calls/analysis", params={"start_date": "2026-10-01", "end_date": "2026-10-31", "open_cases": "true", "target_page": "calls"})
    assert r.status_code == 200, r.text
    payload = r.json()
    items = payload["items"] if isinstance(payload, dict) else payload
    ids = {row["id"] for row in items}
    assert items, payload
    assert keep["analysis_id"] in ids and a["analysis_id"] not in ids and b["analysis_id"] not in ids


def test_closing_notifications_for_other_agents_are_unchanged_and_not_duplicated(env):
    env.as_user("Priyanka")
    a, mine = env.log("Priyanka"), env.log("Priyanka", "H-1B visa")
    ravi, meena = env.log("Ravi", "H-1B"), env.log("Meena", "H-1B extension")
    env.save_status(a, "Success GE")
    env.save_status(a, "Success GE", expected="Success GE")                      # a re-save: still one notification each
    before = env.q("SELECT recipient_id::text, notification_type, title, message, change_context FROM task_notifications ORDER BY recipient_id")
    assert {r[0] for r in before} == {env.users["Ravi"], env.users["Meena"]} and len(before) == 2
    _confirm(env, env.offer(a).json())
    _confirm(env, env.offer(a).json())                                                # nothing left, no new rows
    after = env.q("SELECT recipient_id::text, notification_type, title, message, change_context FROM task_notifications ORDER BY recipient_id")
    assert after == before                                                            # the confirm adds / changes no notification
    assert all(r[1] == "case_closed_by_another_agent" and r[2] == "Case Closed by Another Agent" for r in after)
    # the other agents still get and can confirm theirs, exactly as before
    env.as_user("Ravi")
    pending = env.client.get("/flintdesk/case-closures/notifications").json()
    assert len(pending) == 1 and pending[0]["change_context"]["closure_status"] == "Success GE"
    assert [l["analysis_id"] for l in pending[0]["change_context"]["logs"]] == [ravi["analysis_id"]]
    assert str(mine["analysis_id"]) not in [str(l["analysis_id"]) for p in pending for l in p["change_context"]["logs"]]                                    # Priyanka's own logs are never in Ravi's offer
    res = env.client.post(f"/flintdesk/case-closures/notifications/{pending[0]['id']}/confirm").json()
    assert res["state"] == "confirmed" and env.status(ravi) == "Success GE"
    assert env.status(meena) is None                                                  # Meena has not confirmed hers


def test_cancel_is_just_closing_the_dialog_nothing_changes_server_side(env):
    env.as_user("Priyanka")
    a, b = env.log("Priyanka"), env.log("Priyanka", "H-1B visa", status="PC Follow Up")
    env.save_status(a, "Dropped")
    env.offer(a)                                                                      # the offer is read-only ...
    assert env.status(b) == "PC Follow Up"
    assert env.q("SELECT count(*) FROM fd_case_closure_links")[0][0] == 0 and env.q("SELECT count(*) FROM fd_case_status_history WHERE analysis_id=:i", i=b["analysis_id"])[0][0] == 0
    assert len(env.offer(a).json()["logs"]) == 1                                      # ... and can be offered again later


def test_partial_failure_reports_exact_successes_and_failures(env):
    env.as_user("Priyanka")
    a = env.log("Priyanka"); ok1, ok2 = env.log("Priyanka"), env.log("Priyanka")
    stale_closed, stale_moved, stale_context = env.log("Priyanka"), env.log("Priyanka"), env.log("Priyanka")
    env.save_status(a, "Success GE")
    offer = env.offer(a).json(); assert len(offer["logs"]) == 5
    with env.Session() as db, db.begin():                          # the world changes while the modal is open
        db.execute(text("UPDATE call_analysis_results SET fd_m_case_status_change='Dropped' WHERE id=:i"), {"i": stale_closed["analysis_id"]})
        db.execute(text("UPDATE calls SET extension='102' WHERE call_id=CAST(:c AS UUID)"), {"c": stale_moved["call_id"]})  # reassigned to Ravi
        db.execute(text("UPDATE call_analysis_results SET fd_a_case_type='Divorce' WHERE id=:i"), {"i": stale_context["analysis_id"]})
    r = _confirm(env, offer)
    assert r.status_code == 200
    body = r.json()
    assert {u["analysis_id"] for u in body["updated"]} == {ok1["analysis_id"], ok2["analysis_id"]}
    assert len(body["skipped"]) == 3 and all(s["reason"] for s in body["skipped"])
    assert env.status(stale_closed) == "Dropped" and env.status(stale_context) is None and env.status(stale_moved) is None


def test_duplicate_and_concurrent_confirmations_close_each_log_once(env):
    env.as_user("Priyanka")
    a = env.log("Priyanka"); logs = [env.log("Priyanka") for _ in range(4)]
    env.save_status(a, "Dropped")
    offer = env.offer(a).json()
    first = _confirm(env, offer).json()
    second = _confirm(env, offer).json()                                                # an immediate duplicate
    assert len(first["updated"]) == 4 and second["updated"] == [] and len(second["skipped"]) == 4
    out = []
    fresh = env.log("Priyanka"); offer2 = env.offer(a).json()
    threads = [threading.Thread(target=lambda: out.append(_confirm(env, offer2).json())) for _ in range(5)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sum(len(o["updated"]) for o in out) == 1 and env.status(fresh) == "Dropped"
    for l in logs + [fresh]:
        assert env.q("SELECT count(*) FROM fd_case_status_history WHERE analysis_id=:i", i=l["analysis_id"])[0][0] == 1


def test_reopening_restores_related_logs_but_not_later_edits_or_unrelated_logs(env):
    env.as_user("Priyanka")
    a = env.log("Priyanka", status="Open")
    b, c = env.log("Priyanka", status="PC Follow Up"), env.log("Priyanka", status="Client Side delay")
    unrelated = env.log("Priyanka", "Divorce", status="Blocker")
    env.save_status(a, "Dropped", expected="Open")
    _confirm(env, env.offer(a).json())
    assert env.status(b) == env.status(c) == "Dropped"
    # the agent edits C afterwards (a deliberate later edit): it must survive the reopen
    assert env.save_status(c, "Success PC", expected="Dropped").status_code == 200
    r = env.save_status(a, "Open", expected="Dropped")                                  # reopen the original
    assert r.status_code == 200
    assert env.status(a) == "Open" and env.status(b) == "PC Follow Up"                  # restored
    assert env.status(c) == "Success PC"                                              # later edit untouched
    assert env.status(unrelated) == "Blocker"
    assert env.q("SELECT count(*) FROM fd_case_closure_links WHERE reverted_at IS NULL")[0][0] == 0
    assert env.offer(a).json()["logs"] == []                                            # not closed any more: nothing offered


def test_reopen_before_confirm_makes_the_offer_stale(env):
    env.as_user("Priyanka")
    a, b = env.log("Priyanka"), env.log("Priyanka")
    env.save_status(a, "Dropped"); offer = env.offer(a).json()
    env.save_status(a, "Open", expected="Dropped")
    r = _confirm(env, offer).json()
    assert r["state"] == "reverted" and r["updated"] == [] and env.status(b) is None


def test_resaving_the_closure_with_another_closing_status(env):
    """Documented behaviour: related logs follow the closure's status at confirm time."""
    env.as_user("Priyanka")
    a, b = env.log("Priyanka"), env.log("Priyanka")
    env.save_status(a, "Dropped"); env.save_status(a, "Success GE", expected="Dropped")
    offer = env.offer(a).json()
    assert offer["closure_status"] == "Success GE"
    _confirm(env, offer)
    assert env.status(b) == "Success GE"


def test_authorization_and_validation(env):
    env.as_user("Priyanka")
    a, b = env.log("Priyanka"), env.log("Priyanka")
    env.save_status(a, "Dropped"); offer = env.offer(a).json()
    ravi_log = env.log("Ravi")
    env.as_user("Ravi")
    assert env.offer(a).json() == {"closure_id": None, "logs": []}                       # not the closer: nothing offered
    assert _confirm(env, offer).status_code == 404                                        # and cannot confirm someone else's closure
    assert env.status(b) is None
    env.as_user("Priyanka")
    r = _confirm(env, offer, ids=[ravi_log["analysis_id"]])                               # an id that is not hers
    assert r.status_code == 200 and r.json()["updated"] == [] and env.status(ravi_log) is None
    assert _confirm(env, offer, ids=[]).status_code == 422                                # schema: at least one id
    assert env.client.post(f"/flintdesk/case-closures/{offer['closure_id']}/own-related/confirm", json={"analysis_ids": ["x"]}).status_code == 422
    assert env.client.post("/flintdesk/case-closures/not-a-uuid/own-related/confirm", json={"analysis_ids": [1]}).status_code == 422
    assert env.client.get("/flintdesk/case-closures/own-related", params={"call_id": "nope"}).status_code == 422
    assert env.client.get("/flintdesk/case-closures/own-related", params={"call_id": str(uuid.uuid4())}).json() == {"closure_id": None, "logs": []}
    assert _confirm(env, {"closure_id": str(uuid.uuid4())}, ids=[1]).status_code == 404


def test_routes_reject_anonymous_callers(env):
    env.app.dependency_overrides.pop(env.get_current_user)
    assert env.client.get("/flintdesk/case-closures/own-related", params={"call_id": str(uuid.uuid4())}).status_code == 401
    assert env.client.post(f"/flintdesk/case-closures/{uuid.uuid4()}/own-related/confirm", json={"analysis_ids": [1]}).status_code == 401
    bad = env.client.get("/flintdesk/case-closures/own-related", params={"call_id": str(uuid.uuid4())}, headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401


def test_confirmation_never_starts_a_second_closure_or_notifies_anyone_new(env):
    env.as_user("Priyanka")
    a = env.log("Priyanka"); env.log("Priyanka"); env.log("Priyanka"); env.log("Ravi")
    env.save_status(a, "Success PC")
    n_closures = env.q("SELECT count(*) FROM fd_case_closures")[0][0]
    n_notes = env.q("SELECT count(*) FROM task_notifications")[0][0]
    _confirm(env, env.offer(a).json())
    assert env.q("SELECT count(*) FROM fd_case_closures")[0][0] == n_closures == 1
    assert env.q("SELECT count(*) FROM task_notifications")[0][0] == n_notes == 1


@pytest.mark.xfail(strict=True, reason="Pre-existing on origin/main (affects other agents' confirmed logs identically): if the closer "
                   "re-saves a different closing status and then reopens, revert_closure only restores logs still holding the "
                   "ORIGINAL closing status, so they stay closed. Not changed here; see the verification report.")
def test_reopen_after_the_closer_changed_the_closing_status_restores_related_logs(env):
    env.as_user("Priyanka")
    a, b = env.log("Priyanka", status="Open"), env.log("Priyanka", status="PC Follow Up")
    env.save_status(a, "Dropped", expected="Open"); _confirm(env, env.offer(a).json())
    env.save_status(a, "Success GE", expected="Dropped")
    env.save_status(a, "Open", expected="Success GE")
    assert env.status(b) == "PC Follow Up"


def test_confirmation_leaves_completion_to_submit_for_the_closer_and_other_agents_alike(env):
    env.as_user("Priyanka")
    a, mine, ravi = env.log("Priyanka"), env.log("Priyanka"), env.log("Ravi")
    env.save_status(a, "Success GE")
    _confirm(env, env.offer(a).json())
    env.as_user("Ravi")
    note = env.client.get("/flintdesk/case-closures/notifications").json()[0]
    env.client.post(f"/flintdesk/case-closures/notifications/{note['id']}/confirm")
    # the closing call, the closer's own related log and another agent's confirmed log: closed, none completed by the confirmation
    assert [env.status(x) for x in (a, mine, ravi)] == ["Success GE"] * 3
    assert [env.completed(x) in (False, None) for x in (a, mine, ravi)] == [True, True, True]


def test_recipient_confirm_response_carries_what_the_calls_table_needs_to_update_in_place(env):
    env.as_user("Priyanka")
    a, ravi = env.log("Priyanka"), env.log("Ravi", status="Blocker")
    env.save_status(a, "Success PC")
    env.as_user("Ravi")
    note = env.client.get("/flintdesk/case-closures/notifications").json()[0]
    body = env.client.post(f"/flintdesk/case-closures/notifications/{note['id']}/confirm").json()
    assert body["state"] == "confirmed" and env.status(ravi) == "Success PC"
    (row,) = body["updated"]
    assert row["call_id"] == ravi["call_id"] and row["new_status"] == "Success PC" and row["previous_status"] == "Blocker"
    assert row["fd_m_date_entry"] and row["fd_m_date_entry"] == env.q("SELECT fd_m_date_entry FROM call_analysis_results WHERE id=:i", i=ravi["analysis_id"])[0][0].isoformat()
    closure = body["closure"]
    assert closure["closing_agent_name"] == "Priyanka" and closure["closure_status"] == "Success PC" and closure["client_name"] == "Jane Roe"
    assert closure["client_number"] == CLIENT_PHONE and closure["case_context"] == "H-1B extension" and closure["closed_at"]
    # the Calls listing (what the table reloads from) now reports the new status and the closure for that row
    rows = env.client.get("/calls/analysis", params={"start_date": "2026-10-01", "end_date": "2026-10-31", "target_page": "calls"}).json()
    mine = next(r for r in rows if r["id"] == ravi["analysis_id"])
    assert mine["fd_m_case_status_change"] == "Success PC" and mine["closure_inherited"]["closing_agent_name"] == "Priyanka"
    # and the open-case list no longer contains it (this is what the Dashboard's open counts read)
    open_rows = env.client.get("/calls/analysis", params={"start_date": "2026-10-01", "end_date": "2026-10-31", "target_page": "calls", "open_cases": "true"}).json()
    assert ravi["analysis_id"] not in {r["id"] for r in open_rows}


# ------------------------------------------------------------------ Calls page "Case Status" filter (server side)
def _analysis(env, **params):
    r = env.client.get("/calls/analysis", params={"start_date": "2026-10-01", "end_date": "2026-10-31", "target_page": "calls", **params})
    assert r.status_code == 200, r.text
    return r.json()


def _ids(env, **params):
    body = _analysis(env, **params)
    items = body["items"] if isinstance(body, dict) else body
    return {row["id"] for row in items}


def test_case_status_filter_individual_values_null_and_case_insensitivity(env):
    env.as_user("Priyanka")
    drop, ge, blank, blocker = env.log("Priyanka", status="Dropped"), env.log("Priyanka", status="Success GE"), env.log("Priyanka"), env.log("Priyanka", status="Blocker")
    ws = env.log("Priyanka", status="   ")                                     # whitespace-only is "not set" too
    assert _ids(env, case_status="Dropped") == {drop["analysis_id"]}
    assert _ids(env, case_status="Success GE") == {ge["analysis_id"]}
    assert _ids(env, case_status="dropped") == {drop["analysis_id"]}        # case-insensitive, like the Open rule
    assert _ids(env, case_status="__none__") == {blank["analysis_id"], ws["analysis_id"]}
    assert _ids(env) == {x["analysis_id"] for x in (drop, ge, blank, blocker, ws)}             # no filter: everything, unchanged
    assert _ids(env, case_status="") == _ids(env)                           # an empty value is "All Case Statuses"
    assert _ids(env, case_status="Same client context") == set()            # a valid option with no calls


def test_case_status_filter_intersects_with_the_other_filters(env):
    env.as_user("Priyanka")
    mine = env.log("Priyanka", status="Blocker"); theirs = env.log("Ravi", status="Blocker"); other = env.log("Priyanka", status="Dropped")
    assert _ids(env, case_status="Blocker") == {mine["analysis_id"], theirs["analysis_id"]}
    assert _ids(env, case_status="Blocker", extension="102") == {theirs["analysis_id"]}              # + extension
    assert _ids(env, case_status="Blocker", agent="Priyanka") == {mine["analysis_id"]}                  # + agent
    assert _ids(env, case_status="Blocker", client_number=CLIENT_PHONE) == {mine["analysis_id"], theirs["analysis_id"]}
    assert _ids(env, case_status="Blocker", client_number="999") == set()
    assert _ids(env, case_status="Blocker", start_date="2026-09-01", end_date="2026-09-30") == set()   # + date range
    assert _ids(env, case_status="Dropped", open_cases="true") == set()                              # + Open Cases (closed statuses are not open)
    assert _ids(env, case_status="Blocker", open_cases="true") == {mine["analysis_id"], theirs["analysis_id"]}
    assert other["analysis_id"] not in _ids(env, case_status="Blocker")


def test_case_status_filter_keeps_pagination_and_totals_consistent(env):
    env.as_user("Priyanka")
    wanted = [env.log("Priyanka", status="Blocker") for _ in range(5)]
    for _ in range(4): env.log("Priyanka", status="Dropped")
    first = _analysis(env, case_status="Blocker", page=1, page_size=2)
    second = _analysis(env, case_status="Blocker", page=2, page_size=2)
    third = _analysis(env, case_status="Blocker", page=3, page_size=2)
    assert first["pagination"]["total_records"] == 5 and first["pagination"]["total_pages"] == 3
    got = [row["id"] for page in (first, second, third) for row in page["items"]]
    assert sorted(got) == sorted(x["analysis_id"] for x in wanted) and len(got) == len(set(got))        # every match once, nothing else
    assert _analysis(env, page=1, page_size=100)["pagination"]["total_records"] == 9                   # unfiltered total unchanged


def test_case_status_filter_rejects_unknown_values_and_covers_every_dropdown_option(env):
    env.as_user("Priyanka")
    bad = env.client.get("/calls/analysis", params={"start_date": "2026-10-01", "end_date": "2026-10-31", "case_status": "Bogus"})
    assert bad.status_code == 422
    assert env.client.get("/calls/", params={"start_date": "2026-10-01", "end_date": "2026-10-31", "case_status": "Bogus"}).status_code == 422
    from routes.flintdesk.calls import VALID_FD_CASE_STATUS_CHANGE
    for status in VALID_FD_CASE_STATUS_CHANGE:                              # the backend list is the single source the filter accepts
        assert env.client.get("/calls/analysis", params={"start_date": "2026-10-01", "end_date": "2026-10-31", "case_status": status}).status_code == 200, status
    assert VALID_FD_CASE_STATUS_CHANGE == {"Open", "Client Side Delay", "Blocker", "Dropped", "Same Client Context",
                                           "Wrong Number Dialed", "Initial email sent (Fee Details shared)", "Initial Email Follow Up", "Fee details shared & FU",
                                           "PC link shared", "PC Follow Up", "Success PC", "Success GE"}


def test_case_status_filter_on_the_calls_list_includes_unanalysed_calls_only_for_not_set(env):
    env.as_user("Priyanka")
    analysed = env.log("Priyanka", status="Blocker")
    from storage import models
    from datetime import time as dtime
    cid = uuid.uuid4()
    with env.Session() as db, db.begin():
        db.add(models.Call(call_id=cid, client_id="CL-1", src=SYSTEM_NUMBER, dst=CLIENT_PHONE, disposition="ANSWERED", extension="101",
                           call_date=date(2026, 10, 9), call_time=dtime(23, 59), call_reference="AF#999"))
    def call_ids(**p):
        r = env.client.get("/calls/", params={"start_date": "2026-10-01", "end_date": "2026-10-31", "target_page": "calls", **p}); assert r.status_code == 200, r.text
        return {row["call_id"] for row in r.json()}
    assert call_ids() == {analysed["call_id"], str(cid)}
    assert call_ids(case_status="Blocker") == {analysed["call_id"]}                    # an unanalysed call has no Case Status
    assert call_ids(case_status="__none__") == {str(cid)}                              # ... so it is "not set"
    assert call_ids(case_status="Dropped") == set()


def test_case_status_filter_sees_a_status_change_immediately_and_a_closure_confirmation(env):
    env.as_user("Priyanka")
    a, b = env.log("Priyanka"), env.log("Priyanka", "H-1B visa", status="PC Follow Up")
    assert _ids(env, case_status="Success GE") == set()
    env.save_status(a, "Success GE"); _confirm(env, env.offer(a).json())
    from utils.ttl_cache import invalidate_read_caches
    invalidate_read_caches()                                                  # main.py's write middleware does this after every successful POST/PATCH
    assert _ids(env, case_status="Success GE") == {a["analysis_id"], b["analysis_id"]}     # the closing call and the related log
    assert b["analysis_id"] not in _ids(env, case_status="PC Follow Up")


# ------------------------------------------------------------------ Open Call Aging = the Calls page "Open Cases" population
TODAY = date(2026, 10, 15)
RANGE = {"start": date(2026, 9, 1), "end": date(2026, 10, 31)}


def _aging(env, **kw):
    from services.open_call_aging import open_call_ages
    with env.Session() as db:
        return open_call_ages(db, **{**RANGE, "today": TODAY, **kw})


def _total(env, **kw):
    from services.open_call_aging import open_call_total
    with env.Session() as db:
        return open_call_total(db, **{**RANGE, **kw})


def _ago(n):
    from datetime import timedelta
    return TODAY - timedelta(days=n)


def test_aging_thresholds_below_at_and_above_and_nesting(env):
    env.as_user("Priyanka")
    for age in (2, 3, 4, 5, 9, 10, 14, 15, 40):
        env.log("Priyanka", day=_ago(age))
    result = _aging(env)
    assert result == {"3": 8, "5": 6, "10": 4, "15": 2}
    assert int(result["3"]) >= int(result["5"]) >= int(result["10"]) >= int(result["15"])
    assert _total(env) == 9                                            # distinct open calls, NOT the sum of the cards (20)
    assert sum(result.values()) > _total(env)


def test_aging_counts_blank_and_only_the_five_open_list_statuses(env):
    env.as_user("Priyanka")
    day = _ago(12)
    open_statuses = [None, "", "Open", "Blocker", "Same client context", "Client Side delay", "Dropped"]
    for status in open_statuses: env.log("Priyanka", status=status, day=day)
    for status in ("PC Follow Up", "initial email followup", "Wrong Number Dialed", "Success GE", "Success PC"): env.log("Priyanka", status=status, day=day)
    env.log("Priyanka", status="Blocker", completed=True, day=day)       # the completion flag does not close a call
    assert _aging(env)["10"] == len(open_statuses) + 1
    assert _total(env) == len(open_statuses) + 1


def test_aging_includes_unanalysed_calls_and_counts_every_call_once(env):
    env.as_user("Priyanka")
    from storage import models
    from datetime import time as dtime
    for age, status in ((4, None), (6, "Blocker"), (11, "Dropped"), (13, "Open")): env.log("Priyanka", status=status, day=_ago(age))
    with env.Session() as db, db.begin():                              # a call nobody has analysed yet: no status, so open
        db.add(models.Call(call_id=uuid.uuid4(), client_id="CL-1", src=SYSTEM_NUMBER, dst=CLIENT_PHONE, disposition="ANSWERED", extension="101",
                           call_date=_ago(20), call_time=dtime(23, 58), call_reference="AF#998"))
    assert _total(env) == 5                                            # four analysed calls (Dropped counts) + the unanalysed one
    assert _aging(env) == {"3": 5, "5": 4, "10": 3, "15": 1}


def test_aging_follows_the_agent_and_date_range_filters_like_the_calls_page(env):
    env.as_user("Priyanka")
    env.log("Priyanka", day=_ago(8)); env.log("Priyanka", day=_ago(12)); env.log("Ravi", day=_ago(12)); env.log("Ravi", day=_ago(30))
    assert _aging(env, agent="Priyanka") == {"3": 2, "5": 2, "10": 1, "15": 0}
    assert _aging(env, agent="Ravi") == {"3": 2, "5": 2, "10": 2, "15": 1}
    assert _aging(env, agent="all") == {"3": 4, "5": 4, "10": 3, "15": 1}
    assert _aging(env, extension="102") == _aging(env, agent="Ravi")
    assert _aging(env, start=_ago(12), end=_ago(8)) == {"3": 3, "5": 3, "10": 2, "15": 0}   # both range ends inclusive
    assert _aging(env, start=_ago(7), end=_ago(1)) == {"3": 0, "5": 0, "10": 0, "15": 0}
    ids = {r["id"] for r in env.client.get("/calls/analysis", params={"start_date": str(_ago(12)), "end_date": str(_ago(8)), "target_page": "calls", "open_cases": "true", "agent": "Priyanka"}).json()}
    assert len(ids) == _total(env, start=_ago(12), end=_ago(8), agent="Priyanka")


def test_aging_drops_a_call_the_moment_it_is_closed_or_confirmed_closed(env):
    env.as_user("Priyanka")
    a, b, c = env.log("Priyanka", day=_ago(12)), env.log("Priyanka", "H-1B visa", day=_ago(12)), env.log("Priyanka", "Divorce", day=_ago(12))
    assert _aging(env)["10"] == 3
    env.save_status(c, "Wrong Number Dialed"); assert _aging(env)["10"] == 2              # open -> not counted
    env.save_status(a, "Success GE")
    assert _aging(env)["10"] == 1                                                        # the closing call itself
    _confirm(env, env.offer(a).json())
    assert _aging(env)["10"] == 0 and _total(env) == 0                                   # the related call closed by confirmation
    env.save_status(a, "Open", expected="Success GE")                                  # reopened: the confirmed log returns too
    assert _aging(env)["10"] == 2


def test_aging_counts_a_call_once_even_when_the_call_log_has_duplicate_rows(env):
    env.as_user("Priyanka")
    from storage import models
    from datetime import time as dtime
    original = env.log("Priyanka", day=_ago(12))
    with env.Session() as db, db.begin():                                               # same date/time/numbers: the same call logged twice
        src = db.get(models.Call, uuid.UUID(original["call_id"]))
        db.add(models.Call(call_id=uuid.uuid4(), client_id="CL-1", src=src.src, dst=src.dst, disposition="ANSWERED", extension="101",
                           call_date=src.call_date, call_time=src.call_time, call_reference="AF#997"))
    assert _total(env) == 1
    assert _aging(env)["10"] == 1


def test_aging_same_client_context_counts_only_open_calls_with_that_status(env):
    env.as_user("Priyanka")
    from services.open_call_aging import same_client_context_ages
    env.log("Priyanka", status="Same client context", day=_ago(4)); env.log("Priyanka", status="Same client context", day=_ago(12))
    env.log("Priyanka", status="Blocker", day=_ago(12)); env.log("Ravi", status="Same client context", day=_ago(20))
    with env.Session() as db:
        mine = same_client_context_ages(db, **RANGE, agent="Priyanka", today=TODAY)
        everyone = same_client_context_ages(db, **RANGE, today=TODAY)
    assert mine == {"3": 2, "5": 1, "10": 1, "15": 0} and everyone == {"3": 3, "5": 2, "10": 2, "15": 1}


def test_reopening_restores_related_logs_that_had_no_status_before_the_closure(env):
    """Regression: restoring a blank status used to violate fd_case_status_history.new_status NOT NULL and roll the whole
    reopen back, leaving the related logs closed (the table here has the production NOT NULL column from migration 017)."""
    env.as_user("Priyanka")
    a, own_blank, ravi_blank = env.log("Priyanka"), env.log("Priyanka", "H-1B visa"), env.log("Ravi")
    env.save_status(a, "Dropped")
    _confirm(env, env.offer(a).json())
    env.as_user("Ravi")
    note = env.client.get("/flintdesk/case-closures/notifications").json()[0]
    env.client.post(f"/flintdesk/case-closures/notifications/{note['id']}/confirm")
    assert env.status(own_blank) == env.status(ravi_blank) == "Dropped"
    env.as_user("Priyanka")
    assert env.save_status(a, "Open", expected="Dropped").status_code == 200
    assert env.status(a) == "Open"
    assert env.status(own_blank) is None and env.status(ravi_blank) is None               # back to "no status"
    assert env.q("SELECT count(*) FROM fd_case_closures WHERE reverted_at IS NOT NULL")[0][0] == 1
    assert env.q("SELECT count(*) FROM fd_case_closure_links WHERE reverted_at IS NULL")[0][0] == 0
    assert env.q("SELECT fd_m_date_entry FROM call_analysis_results WHERE id=:i", i=own_blank["analysis_id"])[0][0] is None


# ------------------------------------------------------------------ AI dependency read (who must act next on an open call)
import json as _json


class FakeLLM:
    """Stands in for FDLLM: answers from keywords in the prompt, records every prompt, can fail or return junk."""
    model = "fake-model"

    def __init__(self):
        self.prompts, self.mode = [], {}

    def _post_with_retry(self, payload):
        user = payload["messages"][1]["content"]
        self.prompts.append(user)
        for marker, behaviour in self.mode.items():
            if marker in user:
                if behaviour == "boom":
                    raise RuntimeError("rate limited")
                content = behaviour
                break
        else:
            if "ASK RAVI" in user: content = '{"depends_on": "other_agent", "reason": "Needs Ravi to confirm."}'
            elif "WAIT FOR CLIENT" in user: content = '{"depends_on": "client_or_external", "reason": "Client must send documents."}'
            elif "NOTHING" in user: content = '{"depends_on": "none", "reason": "Done."}'
            else: content = '{"depends_on": "agent", "reason": "The agent must call back."}'
        class R:
            def json(self_inner): return {"choices": [{"message": {"content": content}}]}
        return R()


def _meta(env, log):
    return env.q("SELECT fd_stage2_ai_meta FROM call_analysis_results WHERE id=:i", i=log["analysis_id"])[0][0]


def _run(env, llm, limit=50):
    from services import dependency_ai
    with env.Session() as db:
        return dependency_ai.run_batch(db, llm, limit=limit, today=TODAY)


@pytest.mark.parametrize("content,expected", [
    ('{"depends_on": "agent", "reason": "x"}', "agent"), ('{"depends_on": "Other Agent"}', "other_agent"),
    ('{"depends_on": "other-agent"}', "other_agent"), ('```json\n{"depends_on": "none"}\n```', "none"),
    ('{"depends_on": "client_or_external", "reason": ""}', "client_or_external"),
])
def test_dependency_answer_accepts_only_the_four_allowed_values(content, expected):
    from services.dependency_ai import parse_result
    assert parse_result(content)["depends_on"] == expected


@pytest.mark.parametrize("content", [None, "", "not json", "[]", '{"depends_on": "Google"}', '{"depends_on": null}', '{"other": "agent"}', '{"depends_on": 1}', '{"depends_on": "agent or other_agent"}'])
def test_dependency_answer_rejects_everything_else(content):
    from services.dependency_ai import parse_result
    assert parse_result(content) is None


def test_prompt_carries_transcript_self_action_item_agent_and_other_agents():
    from services.dependency_ai import build_messages, MAX_TRANSCRIPT_CHARS
    system, user = build_messages(transcript="T" * (MAX_TRANSCRIPT_CHARS + 500), self_action_item="Email the I-797 copy", agent_name="Priyanka",
                                  other_agent_names=["Ravi", "Priyanka", "Meena", ""])
    assert system["role"] == "system" and "depends_on" in system["content"] and "never invent" in system["content"]
    text_ = user["content"]
    assert "The call's agent: Priyanka" in text_ and "Email the I-797 copy" in text_ and "Meena, Ravi" in text_
    assert max(len(run) for run in re.findall(r"T+", text_)) == MAX_TRANSCRIPT_CHARS      # transcript is capped
    assert "Other front-desk agents in the office: Meena, Ravi\n" in text_              # the call's own agent is not listed as "other"


def test_worker_analyses_only_open_calls_with_a_transcript_and_saves_validated_answers(env):
    env.as_user("Priyanka")
    mine = env.log("Priyanka", transcript="client wants a call back", day=_ago(6), action="Call the client")
    other = env.log("Priyanka", transcript="ASK RAVI to approve the fee", day=_ago(7), action="Check with Ravi")
    client = env.log("Priyanka", transcript="WAIT FOR CLIENT to send the passport", day=_ago(8))
    closed = env.log("Priyanka", transcript="closed already", status="Dropped", day=_ago(9))
    no_text = env.log("Priyanka", transcript=None, day=_ago(10))
    inherited = env.log("Priyanka", transcript="confirmed closure", day=_ago(11), status="Open")
    with env.Session() as db, db.begin():
        db.execute(text("INSERT INTO fd_case_closures (id, client_id, closing_analysis_id, closure_status, context_key, closed_at) VALUES (:i,'CL-1',:a,'Dropped','h-1b',now())"), {"i": uuid.uuid4(), "a": closed["analysis_id"]})
        cid = db.execute(text("SELECT id FROM fd_case_closures LIMIT 1")).scalar()
        db.execute(text("INSERT INTO fd_case_closure_links (id, closure_id, analysis_id, call_id, agent_user_id) VALUES (:i,:c,:a,CAST(:call AS UUID),:u)"),
                   {"i": uuid.uuid4(), "c": cid, "a": inherited["analysis_id"], "call": inherited["call_id"], "u": uuid.UUID(env.users["Priyanka"])})
    llm = FakeLLM()
    counts = _run(env, llm)
    assert counts == {"candidates": 3, "saved": 3, "unusable": 0, "failed": 0, "yielded_to_f3": False, "provider_busy": False}
    assert [_meta(env, x)["dependency_ai"]["depends_on"] for x in (mine, other, client)] == ["agent", "other_agent", "client_or_external"]
    assert all(_meta(env, x) is None for x in (closed, no_text, inherited))
    saved = _meta(env, other)["dependency_ai"]
    assert saved["input_hash"] and saved["model"] == "fake-model" and saved["reason"] == "Needs Ravi to confirm."
    prompt = next(p for p in llm.prompts if "ASK RAVI" in p)
    assert "The call's agent: Priyanka" in prompt and "Check with Ravi" in prompt         # the owner by assignment, and the Self Action Item
    assert "Ravi" in prompt and "Meena" in prompt


def test_worker_is_incremental_reanalyses_changed_calls_and_keeps_stage2_meta(env):
    env.as_user("Priyanka")
    a = env.log("Priyanka", transcript="call back please", day=_ago(6))
    with env.Session() as db, db.begin():
        db.execute(text("""UPDATE call_analysis_results SET fd_stage2_ai_meta = CAST('{"fd_m_self_action_item": {"source": "ai", "value": "x"}}' AS JSONB) WHERE id=:i"""), {"i": a["analysis_id"]})
    llm = FakeLLM()
    assert _run(env, llm)["saved"] == 1
    assert set(_meta(env, a)) == {"fd_m_self_action_item", "dependency_ai"}                  # Stage 2's own record is untouched
    assert _run(env, llm)["candidates"] == 0 and len(llm.prompts) == 1                        # unchanged inputs: no second paid request
    with env.Session() as db, db.begin():
        db.execute(text("UPDATE call_analysis_results SET fd_m_self_action_item='ASK RAVI first' WHERE id=:i"), {"i": a["analysis_id"]})
    assert _run(env, llm)["saved"] == 1 and _meta(env, a)["dependency_ai"]["depends_on"] == "other_agent"
    assert "fd_m_self_action_item" in _meta(env, a)                                            # still there after the re-analysis
    env.save_status(a, "Dropped")
    assert _run(env, llm)["candidates"] == 0                                                   # a closed call is never analysed


def test_worker_never_stores_junk_and_one_failure_does_not_stop_the_batch(env):
    env.as_user("Priyanka")
    good = env.log("Priyanka", transcript="GOOD call", day=_ago(6))
    junk = env.log("Priyanka", transcript="JUNK call", day=_ago(7))
    boom = env.log("Priyanka", transcript="BOOM call", day=_ago(8))
    llm = FakeLLM(); llm.mode = {"JUNK": '{"depends_on": "Google"}', "BOOM": "boom"}
    counts = _run(env, llm)
    assert counts == {"candidates": 3, "saved": 1, "unusable": 1, "failed": 1, "yielded_to_f3": False, "provider_busy": False}
    assert _meta(env, good)["dependency_ai"]["depends_on"] == "agent"
    assert "dependency_ai" not in (_meta(env, junk) or {}) and "dependency_ai" not in (_meta(env, boom) or {})   # left unanalysed, never guessed
    llm.mode = {}
    with env.Session() as db, db.begin():                                                      # (the retry back-off is covered below)
        db.execute(text("UPDATE call_analysis_results SET fd_stage2_ai_meta = fd_stage2_ai_meta - 'dependency_ai_failed' WHERE fd_stage2_ai_meta ? 'dependency_ai_failed'"))
    assert _run(env, llm)["saved"] == 2                                                        # the next run picks them up


def test_calls_younger_than_three_days_are_never_analysed(env):
    env.as_user("Priyanka")
    young, edge, old = (env.log("Priyanka", transcript="t", day=_ago(n)) for n in (1, 2, 3))
    llm = FakeLLM()
    assert _run(env, llm)["candidates"] == 1 and _meta(env, old)["dependency_ai"] and _meta(env, young) is None and _meta(env, edge) is None


def test_failed_calls_are_retried_a_few_times_with_a_back_off_never_forever(env):
    from services import dependency_ai
    env.as_user("Priyanka")
    bad = env.log("Priyanka", transcript="BOOM call", day=_ago(6))
    llm = FakeLLM(); llm.mode = {"BOOM": "boom"}
    assert _run(env, llm)["failed"] == 1
    assert _run(env, llm)["candidates"] == 0 and len(llm.prompts) == 1                         # inside the back-off window: no second paid request
    for attempt in range(2, dependency_ai.MAX_ATTEMPTS + 1):                                    # the window passes -> one more try each time
        with env.Session() as db, db.begin():
            db.execute(text("UPDATE call_analysis_results SET fd_stage2_ai_meta = jsonb_set(fd_stage2_ai_meta, '{dependency_ai_failed,last_at}', '\"2020-01-01T00:00:00Z\"') WHERE id=:i"), {"i": bad["analysis_id"]})
        assert _run(env, llm)["failed"] == 1
    with env.Session() as db, db.begin():
        db.execute(text("UPDATE call_analysis_results SET fd_stage2_ai_meta = jsonb_set(fd_stage2_ai_meta, '{dependency_ai_failed,last_at}', '\"2020-01-01T00:00:00Z\"') WHERE id=:i"), {"i": bad["analysis_id"]})
    assert _run(env, llm)["candidates"] == 0 and len(llm.prompts) == dependency_ai.MAX_ATTEMPTS  # gave up: attempts used up
    assert _meta(env, bad)["dependency_ai_failed"]["attempts"] == dependency_ai.MAX_ATTEMPTS
    with env.Session() as db, db.begin():                                                       # the call changes -> a fresh start
        db.execute(text("UPDATE call_analysis_results SET fd_m_self_action_item='something new' WHERE id=:i"), {"i": bad["analysis_id"]})
    llm.mode = {}
    assert _run(env, llm)["saved"] == 1


def test_job_steps_aside_while_f3_has_calls_in_flight_and_resumes_when_it_is_done(env):
    from storage import models
    env.as_user("Priyanka")
    waiting = env.log("Priyanka", transcript="t", day=_ago(6))
    with env.Session() as db, db.begin():                                                       # F3 has a fresh transcribed call it has not processed
        fresh = uuid.uuid4()
        db.add(models.Call(call_id=fresh, client_id="CL-1", src=SYSTEM_NUMBER, dst=CLIENT_PHONE, disposition="ANSWERED", extension="101",
                           call_date=datetime.now(timezone.utc).date(), call_time=dtime(10, 0), transcript="fresh call", fd_processing_status="not processed",
                           registered_at=datetime.now(timezone.utc)))
    llm = FakeLLM()
    counts = _run(env, llm)
    assert counts["yielded_to_f3"] is True and counts["saved"] == 0 and llm.prompts == []      # zero model requests while F3 is working
    with env.Session() as db, db.begin():
        db.execute(text("UPDATE calls SET fd_processing_status='processed' WHERE call_id=:c"), {"c": fresh})
    assert _run(env, llm)["saved"] == 1 and len(llm.prompts) == 1


def test_a_provider_slow_down_stops_this_job_and_never_touches_f3s_rate_limiter(env):
    from services import dependency_ai
    from services.Processing_pipeline import fd_llm_node
    env.as_user("Priyanka")
    for n in (6, 7, 8):
        env.log("Priyanka", transcript=f"call {n}", day=_ago(n))
    class Busy(FakeLLM):
        def _post_with_retry(self, payload):
            self.prompts.append("x")
            raise dependency_ai.ProviderBusy("429")
    llm, capacity, hits = Busy(), fd_llm_node._RATE_LIMITER.capacity, fd_llm_node._rate_limit_hits["count"]
    counts = _run(env, llm)
    assert counts["provider_busy"] is True and len(llm.prompts) == 1                           # stopped at the first 429, did not hammer the rest
    assert fd_llm_node._RATE_LIMITER.capacity == capacity and fd_llm_node._rate_limit_hits["count"] == hits   # F3's shared limiter untouched
    assert _run(env, FakeLLM())["saved"] == 3                                                  # nothing was marked as failed: the next run just continues


def test_dependency_job_never_holds_f3s_lock_or_uses_f3s_client():
    import ast, inspect
    from scheduler import jobs
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(jobs))) if isinstance(n, ast.FunctionDef) and n.name == "run_open_call_dependency_ai")
    decorators = {ast.unparse(d) for d in fn.decorator_list}
    used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert not any("skip_if_stages_busy" in d for d in decorators)         # it never holds F3_LOCK, so F3's fallback and daily sync are never skipped because of it
    assert {"F3_LOCK", "FDLLM", "F3Processor"}.isdisjoint(used) and "DependencyLLM" in used


def test_dependency_cards_count_saved_answers_per_threshold_for_the_selected_agent(env):
    env.as_user("Priyanka")
    from services.open_call_aging import dependency_ages
    rows = [("Priyanka", 2, "agent"), ("Priyanka", 4, "agent"), ("Priyanka", 12, "agent"), ("Priyanka", 6, "other_agent"), ("Priyanka", 16, "other_agent"),
            ("Priyanka", 9, "client_or_external"), ("Priyanka", 9, "none"), ("Priyanka", 30, None), ("Ravi", 12, "agent"), ("Ravi", 12, "other_agent")]
    for agent, age, value in rows:
        l = env.log(agent, day=_ago(age), transcript="t")
        if value:
            with env.Session() as db, db.begin():
                db.execute(text("UPDATE call_analysis_results SET fd_stage2_ai_meta = CAST(:m AS JSONB) WHERE id=:i"),
                           {"m": _json.dumps({"dependency_ai": {"depends_on": value, "input_hash": "x"}}), "i": l["analysis_id"]})
    closed = env.log("Priyanka", day=_ago(12), status="Success GE", transcript="t")
    with env.Session() as db, db.begin():
        db.execute(text("UPDATE call_analysis_results SET fd_stage2_ai_meta = CAST(:m AS JSONB) WHERE id=:i"), {"m": _json.dumps({"dependency_ai": {"depends_on": "agent"}}), "i": closed["analysis_id"]})
    with env.Session() as db:
        priya = dependency_ages(db, **RANGE, agent="Priyanka", today=TODAY)
        ravi = dependency_ages(db, **RANGE, agent="Ravi", today=TODAY)
    assert priya["agentDependency"] == {"3": 2, "5": 1, "10": 1, "15": 0}                       # ages 4 and 12 (2 is under 3 days; the Dropped one is closed)
    assert priya["otherAgentDependency"] == {"3": 2, "5": 2, "10": 1, "15": 1}                  # ages 6 and 16
    assert priya["unanalysed"] == 1                                                              # the 30-day-old call has no saved answer yet
    assert ravi["agentDependency"] == {"3": 1, "5": 1, "10": 1, "15": 0} and ravi["otherAgentDependency"] == {"3": 1, "5": 1, "10": 1, "15": 0}


def test_open_call_aging_is_its_own_request_and_never_part_of_the_dashboard_summary(env):
    import inspect
    from services import dashboard_summary
    assert "open_call_aging" not in inspect.getsource(dashboard_summary.build_summary)        # the summary does not wait for it
    assert "open_call_age" not in inspect.getsource(dashboard_summary._run_calculator)
    from routes.flintdesk import dashboard_metrics
    assert "/api/dashboard/open-call-aging" in {route.path for route in dashboard_metrics.router.routes}


def test_open_call_aging_payload_shape_and_agent_gating(env):
    from services.dashboard_summary import open_call_aging_payload
    env.as_user("Priyanka"); env.log("Priyanka", day=_ago(12), status="Same client context")
    with env.Session() as db:
        everyone = open_call_aging_payload(db, start=RANGE["start"], end=RANGE["end"], agent="all")
        one = open_call_aging_payload(db, start=RANGE["start"], end=RANGE["end"], agent="Priyanka")
    assert set(everyone) == {"openCallAges", "openCallAgeContext"} and set(everyone["openCallAges"]) == {"3", "5", "10", "15"}
    assert set(everyone["openCallAgeContext"]) == {"sameClientContext"}                          # All Agents: no dependency figures
    assert {"agentDependency", "otherAgentDependency", "unanalysed", "agent", "aiEnabled"} <= set(one["openCallAgeContext"])
    assert one["openCallAgeContext"]["sameClientContext"]["10"] == 1


def test_open_call_aging_route_is_authenticated_and_validates_the_range(env):
    from routes.flintdesk import dashboard_metrics
    env.app.include_router(dashboard_metrics.router)
    env.as_user("Priyanka"); env.log("Priyanka", day=date(2026, 10, 9))
    ok = env.client.get("/api/dashboard/open-call-aging", params={"start_date": "2026-10-01", "end_date": "2026-10-31", "agent": "Priyanka"})
    assert ok.status_code == 200 and set(ok.json()) == {"openCallAges", "openCallAgeContext"}
    assert env.client.get("/api/dashboard/open-call-aging", params={"start_date": "2026-10-31", "end_date": "2026-10-01"}).status_code == 400
    assert env.client.get("/api/dashboard/open-call-aging", params={"start_date": "2026-01-01", "end_date": "2026-10-31"}).status_code == 400
    env.app.dependency_overrides.pop(env.get_current_user)
    assert env.client.get("/api/dashboard/open-call-aging", params={"start_date": "2026-10-01", "end_date": "2026-10-31"}).status_code == 401


def test_single_pass_aging_bundle_matches_the_three_separate_reads_exactly(env):
    from services.open_call_aging import aging_bundle, dependency_ages, open_call_ages, same_client_context_ages
    env.as_user("Priyanka")
    plan = [("Priyanka", 2, "agent", None), ("Priyanka", 4, "agent", "Same client context"), ("Priyanka", 12, "other_agent", "Same client context"),
            ("Priyanka", 6, "other_agent", None), ("Priyanka", 16, "client_or_external", "Blocker"), ("Priyanka", 30, None, "Same client context"),
            ("Ravi", 12, "agent", "Same client context"), ("Ravi", 20, None, None), ("Priyanka", 9, "agent", "Dropped")]
    for owner, age, value, status in plan:
        l = env.log(owner, day=_ago(age), status=status, transcript="t")
        if value:
            with env.Session() as db, db.begin():
                db.execute(text("UPDATE call_analysis_results SET fd_stage2_ai_meta = CAST(:m AS JSONB) WHERE id=:i"),
                           {"m": _json.dumps({"dependency_ai": {"depends_on": value, "input_hash": "x"}}), "i": l["analysis_id"]})
    with env.Session() as db:
        for agent in ("all", "Priyanka", "Ravi"):
            kw = {**RANGE, "agent": agent}
            bundle = aging_bundle(db, **kw, today=TODAY)
            assert bundle["ages"] == open_call_ages(db, **kw, today=TODAY), agent
            assert bundle["sameClientContext"] == same_client_context_ages(db, **kw, today=TODAY), agent
            if agent != "all":
                old = dependency_ages(db, **kw, today=TODAY)
                assert {k: bundle[k] for k in ("agentDependency", "otherAgentDependency", "unanalysed")} == old, agent
            else:
                assert "agentDependency" not in bundle


def test_ai_reads_only_calls_from_the_start_date_onward_and_older_ones_are_never_waiting(env, monkeypatch):
    from services.open_call_aging import dependency_ages
    monkeypatch.setenv("OPEN_CALL_DEPENDENCY_AI_SINCE", "2026-10-14")
    env.as_user("Priyanka")
    old = env.log("Priyanka", transcript="old call", day=date(2026, 10, 13))
    first = env.log("Priyanka", transcript="first day", day=date(2026, 10, 14))
    newer = env.log("Priyanka", transcript="ASK RAVI please", day=date(2026, 10, 15))
    llm = FakeLLM()
    from services import dependency_ai
    with env.Session() as db:
        counts = dependency_ai.run_batch(db, llm, today=date(2026, 10, 20))
    assert counts["candidates"] == 2 and counts["saved"] == 2                         # 14 and 15 Oct only
    assert _meta(env, old) is None and _meta(env, first)["dependency_ai"]["depends_on"] == "agent"
    assert _meta(env, newer)["dependency_ai"]["depends_on"] == "other_agent"
    assert len(llm.prompts) == 2 and not any("old call" in p for p in llm.prompts)    # the 13 Oct call was never sent to the model
    env.log("Priyanka", transcript="not analysed yet", day=date(2026, 10, 15))
    with env.Session() as db:
        out = dependency_ages(db, start=date(2026, 9, 1), end=date(2026, 10, 31), agent="Priyanka", today=date(2026, 10, 20))
    assert out["unanalysed"] == 1                                                        # only the new, not-yet-read call; the 13 Oct one is not "waiting"


def test_ai_start_date_default_override_and_bad_value(monkeypatch):
    from datetime import date as _d
    from services.dependency_ai import DEFAULT_AI_START, ai_start_date
    monkeypatch.delenv("OPEN_CALL_DEPENDENCY_AI_SINCE", raising=False)
    assert ai_start_date() == DEFAULT_AI_START == _d(2026, 10, 14)
    monkeypatch.setenv("OPEN_CALL_DEPENDENCY_AI_SINCE", "2026-11-01"); assert ai_start_date() == _d(2026, 11, 1)
    monkeypatch.setenv("OPEN_CALL_DEPENDENCY_AI_SINCE", "not a date"); assert ai_start_date() == DEFAULT_AI_START


def test_aging_payload_tells_the_page_from_which_date_the_ai_reads(env, monkeypatch):
    from services.dashboard_summary import open_call_aging_payload
    monkeypatch.setenv("OPEN_CALL_DEPENDENCY_AI_SINCE", "2026-10-14")
    with env.Session() as db:
        one = open_call_aging_payload(db, start=RANGE["start"], end=RANGE["end"], agent="Priyanka")
        everyone = open_call_aging_payload(db, start=RANGE["start"], end=RANGE["end"], agent="all")
    assert one["openCallAgeContext"]["aiSince"] == "2026-10-14" and "aiSince" not in everyone["openCallAgeContext"]

# ------------------------------------------------------------------ Leads S1/S2: green when the call was made on any day, and "Call made on"
def _lead(env, phone, *, added_at, submitted=True, lead_id=None):
    from storage import models
    lid = uuid.uuid4()
    with env.Session() as db, db.begin():
        db.add(models.FDLead(id=lid, fd_m_leads_lead_id=lead_id or f"LD#{uuid.uuid4().hex[:6]}", fd_m_leads_phone=phone, lead_added_at=added_at,
                             fd_m_leads_owner_id=uuid.UUID(env.users["Priyanka"]) if submitted else None))
    return lid


def _out_call(env, phone, day, hour=11, src=SYSTEM_NUMBER, minute=0):
    from storage import models
    from datetime import time as dtime
    env.n += 1
    with env.Session() as db, db.begin():
        db.add(models.Call(call_id=uuid.uuid4(), client_id="CL-1", src=src, dst=phone, disposition="ANSWERED", extension="101", call_date=day,
                           call_time=dtime(hour, minute), call_reference=f"AF#{5000 + env.n}"))


def _lead_fields(env, lead_id):
    from routes.flintdesk.leads import _lead_rows
    with env.Session() as db:
        item = next(r for r in _lead_rows(db, None) if r["id"] == lead_id)
    return item["outbound_call_id"], item["outbound_call_date"], item["outbound_call_same_day"], item["same_day_outbound_call_id"]


from datetime import timezone as _tz  # noqa: E402
_CT_EVENING = datetime(2026, 10, 13, 0, 30, tzinfo=_tz.utc)    # 12 Oct 19:30 in Chicago (13 Oct in UTC)


def test_leads_same_day_call_is_green_and_has_no_made_on_label(env):
    lead = _lead(env, "3125550177", added_at=datetime(2026, 10, 12, 16, 0, tzinfo=_tz.utc))        # 12 Oct 11:00 CT
    _out_call(env, "3125550177", date(2026, 10, 12))
    call_id, call_date, same_day, same_day_id = _lead_fields(env, lead)
    assert call_id and call_date == "2026-10-12" and same_day is True and same_day_id == call_id


def test_leads_call_made_the_next_day_is_green_and_reports_its_date(env):
    lead = _lead(env, "3125550178", added_at=_CT_EVENING)
    _out_call(env, "3125550178", date(2026, 10, 13), hour=10)
    call_id, call_date, same_day, same_day_id = _lead_fields(env, lead)
    assert call_id and call_date == "2026-10-13" and same_day is False
    assert same_day_id == ""                                              # the Dashboard's "same day" figure is untouched


def test_leads_first_call_after_import_wins_and_calls_before_the_lead_never_count(env):
    lead = _lead(env, "3125550179", added_at=datetime(2026, 10, 14, 16, 0, tzinfo=_tz.utc))        # 14 Oct 11:00 CT
    _out_call(env, "3125550179", date(2026, 8, 30))                                                # long before the lead existed
    _out_call(env, "3125550179", date(2026, 10, 13))                                               # the day before the import
    assert _lead_fields(env, lead) == ("", "", False, "")
    _out_call(env, "3125550179", date(2026, 10, 17), hour=9)
    _out_call(env, "3125550179", date(2026, 10, 16), hour=15)
    _out_call(env, "3125550179", date(2026, 10, 16), hour=9)
    call_id, call_date, same_day, _ = _lead_fields(env, lead)
    assert call_date == "2026-10-16" and same_day is False                                         # the earliest call on/after the import date


def test_leads_same_day_and_later_calls_together_are_same_day(env):
    lead = _lead(env, "3125550180", added_at=datetime(2026, 10, 12, 16, 0, tzinfo=_tz.utc))
    _out_call(env, "3125550180", date(2026, 10, 14)); _out_call(env, "3125550180", date(2026, 10, 12), hour=15)
    call_id, call_date, same_day, same_day_id = _lead_fields(env, lead)
    assert call_date == "2026-10-12" and same_day is True and same_day_id == call_id


def test_leads_not_submitted_inbound_and_non_system_calls_are_never_green(env):
    unsubmitted = _lead(env, "3125550181", added_at=datetime(2026, 10, 12, 16, 0, tzinfo=_tz.utc), submitted=False)
    _out_call(env, "3125550181", date(2026, 10, 12))
    assert _lead_fields(env, unsubmitted) == ("", "", False, "")                                  # same rule as before: the assignment must be submitted
    other_src = _lead(env, "3125550182", added_at=datetime(2026, 10, 12, 16, 0, tzinfo=_tz.utc))
    _out_call(env, "3125550182", date(2026, 10, 12), src="9998887777")                            # not one of the configured numbers
    assert _lead_fields(env, other_src) == ("", "", False, "")
    inbound = _lead(env, "3125550183", added_at=datetime(2026, 10, 12, 16, 0, tzinfo=_tz.utc))
    from storage import models
    from datetime import time as dtime
    with env.Session() as db, db.begin():
        db.add(models.Call(call_id=uuid.uuid4(), client_id="CL-1", src="3125550183", dst=SYSTEM_NUMBER, disposition="ANSWERED", extension="101",
                           call_date=date(2026, 10, 12), call_time=dtime(11, 0), call_reference="AF#4999"))
    assert _lead_fields(env, inbound) == ("", "", False, "")


def test_leads_dates_follow_central_time_at_midnight(env):
    # 13 Oct 04:30 UTC = 12 Oct 23:30 in Chicago, so the lead's assigned date is 12 Oct (not 13 Oct)
    lead = _lead(env, "3125550184", added_at=datetime(2026, 10, 13, 4, 30, tzinfo=_tz.utc))
    _out_call(env, "3125550184", date(2026, 10, 12), hour=22)
    call_id, call_date, same_day, _ = _lead_fields(env, lead)
    assert call_date == "2026-10-12" and same_day is True
    late = _lead(env, "3125550185", added_at=datetime(2026, 10, 13, 4, 30, tzinfo=_tz.utc))
    _out_call(env, "3125550185", date(2026, 10, 13), hour=9)
    assert _lead_fields(env, late)[1:3] == ("2026-10-13", False)                                   # the next Central day: "Call made on 10/13/2026"


def test_wrong_number_dialed_is_saved_and_leaves_the_aging_cards(env):
    env.as_user("Priyanka")
    a, b = env.log("Priyanka", day=_ago(12)), env.log("Priyanka", "H-1B visa", day=_ago(12))
    assert _aging(env)["10"] == 2
    assert env.save_status(a, "Wrong Number Dialed").status_code == 200
    assert env.status(a) == "Wrong Number Dialed"
    assert env.status(b) is None                                                         # no other call is touched
    assert _aging(env) == {"3": 1, "5": 1, "10": 1, "15": 0} and _total(env) == 1


def test_calls_page_dependency_popup_lists_exactly_the_calls_the_cards_count(env, monkeypatch):
    env.as_user("Priyanka")
    import services.open_call_aging as aging
    monkeypatch.setattr(aging, "central_today", lambda: TODAY)
    rows = [("Priyanka", 2, "agent", None), ("Priyanka", 4, "agent", None), ("Priyanka", 12, "agent", None),
            ("Priyanka", 6, "other_agent", None), ("Priyanka", 16, "other_agent", None),
            ("Priyanka", 12, "agent", "Wrong Number Dialed"), ("Priyanka", 12, "agent", "Success GE"),
            ("Priyanka", 30, None, None), ("Ravi", 12, "agent", None), ("Ravi", 12, "other_agent", None)]
    for agent, age, value, status in rows:
        l = env.log(agent, day=_ago(age), transcript="t", status=status)
        if value:
            with env.Session() as db, db.begin():
                db.execute(text("UPDATE call_analysis_results SET fd_stage2_ai_meta = CAST(:m AS JSONB) WHERE id=:i"),
                           {"m": _json.dumps({"dependency_ai": {"depends_on": value, "input_hash": "x"}}), "i": l["analysis_id"]})

    def listed(dependency, days, agent="Priyanka"):
        got = env.client.get("/calls/analysis", params={"start_date": str(RANGE["start"]), "end_date": str(RANGE["end"]), "target_page": "calls",
                                                         "agent": agent, "aging_dependency": dependency, "aging_days": days})
        assert got.status_code == 200
        ids = [row["id"] for row in got.json()]
        assert len(ids) == len(set(ids))                                   # a call is never listed twice
        return len(ids)

    with env.Session() as db:
        from services.open_call_aging import dependency_ages
        cards = dependency_ages(db, **RANGE, agent="Priyanka", today=TODAY)
    for days in (3, 5, 10, 15):
        assert listed("agent", days) == cards["agentDependency"][str(days)]
        assert listed("other_agent", days) == cards["otherAgentDependency"][str(days)]
    assert (listed("agent", 3), listed("agent", 10), listed("other_agent", 3), listed("other_agent", 15)) == (2, 1, 2, 1)
    assert listed("agent", 3, agent="Ravi") == 1 and listed("other_agent", 3, agent="Ravi") == 1       # the selected agent narrows both lists
    assert env.client.get("/calls/analysis", params={"start_date": "2026-10-01", "end_date": "2026-10-31", "aging_dependency": "bogus"}).status_code == 422


def test_case_status_can_be_saved_on_a_call_with_no_audio_transcript_or_answer(env):
    """Stage 1 blank, no audio, no transcript, outbound NO ANSWER: the agent can still choose the Case Status Change."""
    env.as_user("Priyanka")
    a = env.log("Priyanka")
    env.q("SELECT 1")
    with env.Session() as db, db.begin():
        db.execute(text("UPDATE calls SET disposition='NO ANSWER', transcript=NULL, audio_s3_uri=NULL WHERE call_id=CAST(:c AS uuid)"), {"c": a["call_id"]})
    assert env.save_status(a, "Wrong Number Dialed").status_code == 200
    assert env.status(a) == "Wrong Number Dialed"


def test_leads_are_listed_latest_entry_date_first_then_highest_lead_number_and_pages_do_not_overlap(env):
    from services.lead_read_query import filtered_leads
    from storage import models
    ids = ["L-9", "L-10", "L-100", "L-2", "NO-NUMBER", "L-101"]
    with env.Session() as db, db.begin():
        for index, lead_id in enumerate(ids):
            db.add(models.FDLead(id=uuid.uuid4(), fd_m_leads_lead_id=lead_id, fd_m_leads_date_received="2026-10-0%d" % (index % 3 + 1),
                             lead_added_at=datetime.now(timezone.utc)))
    with env.Session() as db:
        everything = [row.fd_m_leads_lead_id for row in filtered_leads(db).all()]
        first = [row.fd_m_leads_lead_id for row in filtered_leads(db, page=1, page_size=3).all()]
        second = [row.fd_m_leads_lead_id for row in filtered_leads(db, page=2, page_size=3).all()]
    # Entry dates: 10-03 L-100 L-101, 10-02 L-10 NO-NUMBER, 10-01 L-9 L-2. Latest date first; within a date the higher number first, IDs
    # without a number after the numbered ones of that date.
    assert everything == ["L-101", "L-100", "L-10", "NO-NUMBER", "L-9", "L-2"]
    assert first + second == everything


def test_calls_page_priority_filter_uses_the_confirmed_priority_results_of_the_call(env):
    from importlib import import_module
    with env.Session() as db, db.begin():
        db.execute(text(import_module("migrations.074_priority_cases").STATEMENTS[0]))
    env.as_user("Priyanka")
    confirmed, uncertain, none, h4ead = (env.log("Priyanka", day=_ago(3)) for _ in range(4))

    def classify(log, status="completed", confirmed_forms=(), uncertain_forms=()):
        with env.Session() as db, db.begin():
            db.execute(text("INSERT INTO call_priority_analysis (call_id, analysis_status, confirmed_forms, uncertain_forms) "
                            "VALUES (CAST(:c AS uuid), :s, :f, :u)"), {"c": log["call_id"], "s": status, "f": list(confirmed_forms), "u": list(uncertain_forms)})

    classify(confirmed, confirmed_forms=["RFE", "I-485"]); classify(uncertain, uncertain_forms=["RFE"])
    classify(h4ead, confirmed_forms=["H4EAD"]); classify(none, status="pending", confirmed_forms=["RFE"])      # not completed: never counts

    def ids(form):
        got = env.client.get("/calls/analysis", params={"start_date": "2026-09-01", "end_date": "2026-10-31", "target_page": "calls", "priority_form": form})
        assert got.status_code == 200, got.text
        return [row["call_id"] for row in got.json()]

    agent_attempt = env.client.get("/calls/analysis", params={"start_date": "2026-09-01", "end_date": "2026-10-31", "priority_form": "RFE"})
    assert agent_attempt.status_code == 403                                  # same reviewers as the Priority Cases page: Admin / Team Lead / SME
    env.who["user"] = {**env.who["user"], "role": "team_lead"}
    assert ids("RFE") == [confirmed["call_id"]] and ids("I-485") == [confirmed["call_id"]]
    assert ids("H4-EAD") == ids("H4EAD") == [h4ead["call_id"]]
    assert ids("I-130") == [] and ids("H4") == []
    assert env.client.get("/calls/analysis", params={"start_date": "2026-09-01", "end_date": "2026-10-31", "priority_form": "RFE;DROP"}).status_code == 422
