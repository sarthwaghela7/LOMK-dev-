"""Cross-agent case closure: context matching, notifications, confirm/dismiss, revert, concurrency.

Pure tests always run. The PostgreSQL tests need a DISPOSABLE local database:
    CASE_CLOSURE_TEST_DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:55433/closure_test
Each test gets its own schema; the production DATABASE_URL is never used.
"""
import os
import sys
import threading
import uuid
from datetime import date
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import case_closure as cc  # noqa: E402


# ------------------------------------------------------------------ pure
@pytest.mark.parametrize("a,b", [("H-1B visa", "H-1B extension"), ("H-1B", "h1b transfer"), ("Green card", "I-485 filing"),
                                 ("H-4 EAD", "H4 dependent")])
def test_spellings_of_one_matter_share_a_context(a, b):
    assert cc.context_key(a)[0] == cc.context_key(b)[0]


@pytest.mark.parametrize("a,b", [("H-1B", "H-4 EAD"), ("Green card", "H-1B"), ("RFE", "Citizenship")])
def test_different_matters_never_share_a_context(a, b):
    assert cc.context_key(a)[0] != cc.context_key(b)[0]


@pytest.mark.parametrize("generic", [None, "", "  ", "Immigration", "immigration consultation", "Visa", "N/A", "unknown", "abc"])
def test_blank_or_generic_text_is_not_a_context(generic):
    assert cc.context_key(generic) is None


def test_only_the_three_closure_statuses_close():
    assert [cc.closure_status_of(v) for v in ("Dropped", " success - ge ", "Success PC")] == ["Dropped", "Success GE", "Success PC"]
    for other in (None, "", "Open", "Same Client Context", "Blocker", "PC Follow Up", "Client Side Delay"):
        assert cc.closure_status_of(other) is None


def test_message_names_closer_context_status_and_client():
    text_ = cc.closure_message({"closing_agent_name": "Priya", "case_context": "H-1B extension", "client_name": "Jane Roe",
                                "client_number": "3125550100", "client_id": "CL-1", "closure_status": "Success GE"})
    assert all(part in text_ for part in ("Priya", "H-1B extension", "Jane Roe", "3125550100", "Success GE", "stop counting as open", "X to leave them unchanged"))


def test_routes_are_authenticated():
    from middleware.auth import get_current_user
    from routes.flintdesk import case_closures
    paths = {route.path for route in case_closures.router.routes}
    assert paths == {"/flintdesk/case-closures/notifications", "/flintdesk/case-closures/own-related",
                     "/flintdesk/case-closures/{closure_id}/own-related/confirm",
                     "/flintdesk/case-closures/notifications/{notification_id}/confirm",
                     "/flintdesk/case-closures/notifications/{notification_id}/dismiss"}
    for route in case_closures.router.routes:
        assert get_current_user in [dep.call for dep in route.dependant.dependencies]


def test_open_cases_filter_also_excludes_inherited_closures():
    from sqlalchemy.dialects import postgresql
    from services import analysis_read_query as q
    src = open(q.__file__).read()
    assert "FDCaseClosureLink" in src and "inherited_closure" in src


# ------------------------------------------------------------------ PostgreSQL
@pytest.fixture
def world(monkeypatch):
    url = os.getenv("CASE_CLOSURE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set CASE_CLOSURE_TEST_DATABASE_URL to a disposable local PostgreSQL database")
    assert make_url(url).host in {"localhost", "127.0.0.1"}, "Tests require local PostgreSQL"
    schema = "test_cc_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema}"})
    with engine.begin() as conn:
        for ddl in (
            "CREATE TABLE clients (client_id text primary key, client_name text, phone_number text)",
            "CREATE TABLE calls (call_id uuid primary key, client_id text, call_reference text, call_date date)",
            "CREATE TABLE call_analysis_results (id bigserial primary key, call_id uuid, fd_a_case_type text, case_type text,"
            " fd_m_case_status_change text, case_closing_completed boolean, front_desk_person_name text,"
            " fd_m_date_entry date, manual_data_score double precision, manual_data_score_reason text, manual_data_scored_at timestamptz)",
            "CREATE TABLE fd_case_status_history (id bigserial primary key, analysis_id bigint, call_id uuid, previous_status text,"
            " new_status text, changed_at timestamptz default now(), changed_by uuid)",
            "CREATE TABLE task_notifications (id uuid primary key, task_id uuid, lead_id uuid, recipient_id uuid not null,"
            " message text not null, title text not null default 'Notification', notification_type text not null default 'x',"
            " source_page text, entity_id text, change_context jsonb, created_at timestamptz not null, read_at timestamptz)",
        ):
            conn.execute(text(ddl))
    for module, function in (("migrations.071_create_case_closures", "migrate_create_case_closures"),
                             ("migrations.072_notification_closure_columns", "migrate_notification_closure_columns")):
        module = import_module(module)
        monkeypatch.setattr(module, "engine", engine)
        getattr(module, function)()
    Session = sessionmaker(bind=engine)
    session_factory = Session

    class World:
        def __init__(self):
            self.owners = {}
            self.users = {name: str(uuid.uuid4()) for name in ("Priya", "Ravi", "Meena", "Gokul")}
            self.names = {v: k for k, v in self.users.items()}
            self.n = 0
        Session = staticmethod(session_factory)

        def resolver(self, db, rows):
            return {r["analysis_id"]: self.owners[r["analysis_id"]] for r in rows if r["analysis_id"] in self.owners}

        def client(self, client_id="CL-1", name="Jane Roe", phone="3125550100"):
            with Session() as db, db.begin():
                db.execute(text("INSERT INTO clients VALUES (:c,:n,:p) ON CONFLICT DO NOTHING"), {"c": client_id, "n": name, "p": phone})
            return client_id

        def log(self, agent, case_type, client_id="CL-1", status=None, completed=False, day=date(2026, 10, 9), second_type=None):
            self.n += 1
            call_id = uuid.uuid4()
            with Session() as db, db.begin():
                db.execute(text("INSERT INTO calls VALUES (:c,:cl,:ref,:d)"),
                           {"c": call_id, "cl": client_id, "ref": f"AF#{1000 + self.n}", "d": day})
                aid = db.execute(text("""INSERT INTO call_analysis_results (call_id, fd_a_case_type, case_type, fd_m_case_status_change, case_closing_completed)
                    VALUES (:c,:t,:u,:s,:k) RETURNING id"""), {"c": call_id, "t": case_type, "u": second_type, "s": status, "k": completed}).scalar_one()
            self.owners[aid] = (self.users[agent], agent)
            return SimpleNamespace(id=aid, call_id=call_id, client_id=client_id, fd_a_case_type=case_type, case_type=second_type, agent=agent, call_date=day)

        def close(self, log, status="Success GE", db=None):
            """Mimic a status save: write the new status, then run the hook in the same transaction."""
            own = db is None
            db = db or Session()
            try:
                previous = db.execute(text("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id=:i"), {"i": log.id}).scalar()
                db.execute(text("UPDATE call_analysis_results SET fd_m_case_status_change=:s WHERE id=:i"), {"s": status, "i": log.id})
                user = {"fd_user_id": self.users[log.agent], "name": log.agent}
                result = cc.on_status_saved(db, log, log, user, previous, status, owner_resolver=self.resolver)
                if own:
                    db.commit()
                return result
            finally:
                if own:
                    db.close()

        def notes(self, recipient=None, kind=None):
            sql = "SELECT recipient_id::text r, notification_type t, dismissed_at, confirmed_at, change_context c, message FROM task_notifications ORDER BY created_at"
            with Session() as db:
                rows = db.execute(text(sql)).mappings().all()
            return [r for r in rows if (recipient is None or r["r"] == self.users[recipient]) and (kind is None or r["t"] == kind)]

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


def test_closing_notifies_only_other_agents_with_the_same_context(world):
    world.client()
    mine = world.log("Priya", "H-1B extension")
    same = world.log("Ravi", "H-1B visa")
    other_context = world.log("Meena", "H-4 EAD")            # same client, different matter
    generic = world.log("Gokul", "Immigration")             # no usable context
    result = world.close(mine, "Success GE")
    assert result["notified"] == 1
    notes = world.notes()
    assert [world.names[n["r"]] for n in notes] == ["Ravi"]            # not Priya, Meena or Gokul
    ctx = notes[0]["c"]
    assert ctx["closing_agent_name"] == "Priya" and ctx["closure_status"] == "Success GE"
    assert ctx["client_name"] == "Jane Roe" and ctx["client_number"] == "3125550100" and ctx["case_context"] == "H-1B extension"
    assert [log["analysis_id"] for log in ctx["logs"]] == [same.id]
    assert "Priya" in notes[0]["message"] and "Success GE" in notes[0]["message"]


@pytest.mark.parametrize("status", ["Dropped", "Success GE", "Success PC"])
def test_each_closure_status_triggers(world, status):
    world.client()
    world.log("Ravi", "Green card")
    assert world.close(world.log("Priya", "I-485"), status)["notified"] == 1


@pytest.mark.parametrize("status", ["Open", "Blocker", "PC Follow Up", "Same client context", None])
def test_non_closure_statuses_do_nothing(world, status):
    world.client()
    world.log("Ravi", "H-1B")
    assert world.close(world.log("Priya", "H-1B"), status) is None
    assert world.notes() == [] and world.scalar("SELECT count(*) FROM fd_case_closures") == 0


def test_other_clients_and_generic_closers_are_untouched(world):
    world.client(); world.client("CL-2", "John Doe", "3125550199")
    world.log("Ravi", "H-1B", client_id="CL-2")                     # different client, same context
    world.log("Meena", "H-1B")
    generic_closer = world.log("Priya", "Immigration")             # closer has no usable context -> nothing to sync
    assert world.close(generic_closer) is None
    assert world.notes() == []
    assert world.close(world.log("Priya", "H-1B"))["notified"] == 1  # only Meena (same client)
    assert [world.names[n["r"]] for n in world.notes()] == ["Meena"]


def test_independently_closed_logs_and_the_closers_own_logs_are_not_offered(world):
    world.client()
    world.log("Ravi", "H-1B", status="Dropped")                    # already independently closed
    world.log("Meena", "H-1B", status="Success PC")              # already independently closed
    world.log("Gokul", "H-1B", status="Success GE")              # already independently closed
    world.log("Priya", "H-1B")                                     # the closer's own other log
    assert world.close(world.log("Priya", "H-1B"))["notified"] == 0
    assert world.notes() == []


@pytest.mark.parametrize("status", [None, "", "Open", "Initial email sent.", "PC Follow Up", "PC shared and lost",
                                    "Fee details shared & FU", "Client Side delay", "Move to next date", "Blocker",
                                    "Same client context", "initial email followup", "N/A"])
def test_open_means_any_status_except_the_three_closures(world, status):
    world.client()
    world.log("Ravi", "H-1B", status=status)
    assert world.close(world.log("Priya", "H-1B"))["notified"] == 1


def test_the_mark_completed_flag_alone_does_not_make_a_log_closed(world):
    # Older records could carry the completed flag with a non-closure status; only the status decides "open".
    world.client()
    world.log("Ravi", "H-1B", status="Open", completed=True)
    assert world.close(world.log("Priya", "H-1B"))["notified"] == 1


def test_resaving_the_same_closure_creates_no_duplicates(world):
    world.client()
    world.log("Ravi", "H-1B")
    closer = world.log("Priya", "H-1B")
    world.close(closer, "Success GE")
    again = world.close(closer, "Dropped")                         # same record, other closure status
    assert again["existing"] is True and again["notified"] == 0
    assert world.scalar("SELECT count(*) FROM fd_case_closures") == 1
    assert len(world.notes()) == 1 and world.scalar("SELECT closure_status FROM fd_case_closures") == "Dropped"
    assert world.scalar("SELECT closing_agent_name FROM fd_case_closures") == "Priya"


def test_confirm_sets_only_the_listed_log_to_the_closure_status_and_records_who(world):
    world.client()
    ravi = world.log("Ravi", "H-1B visa", status="In Progress")
    other_log = world.log("Ravi", "Divorce")                           # same agent, different matter: untouched
    other_agent = world.log("Meena", "H-1B", status="Open")            # another agent's log: untouched
    world.close(world.log("Priya", "H-1B"), "Success PC")
    note = world.notes("Ravi")[0]
    with world.Session() as db, db.begin():
        result = cc.confirm_notification(db, world.scalar("SELECT id FROM task_notifications WHERE recipient_id=:r", r=world.users["Ravi"]),
                                         world.users["Ravi"], owner_resolver=world.resolver)
    assert result["state"] == "confirmed" and result["closed_by"] == "Priya" and result["closure_status"] == "Success PC"
    assert [(u["previous_status"], u["new_status"]) for u in result["updated"]] == [("In Progress", "Success PC")]
    # The unique call id comes back so the client can open exactly this call record.
    assert result["updated"][0]["analysis_id"] == ravi.id and result["updated"][0]["call_id"]
    assert world.scalar("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id=:i", i=ravi.id) == "Success PC"
    assert world.scalar("SELECT fd_m_date_entry FROM call_analysis_results WHERE id=:i", i=ravi.id) is not None
    assert world.scalar("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id=:i", i=other_log.id) is None
    assert world.scalar("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id=:i", i=other_agent.id) == "Open"
    rows = world_rows(world, "SELECT previous_status, new_status, changed_by::text FROM fd_case_status_history WHERE analysis_id=:a", a=ravi.id)
    assert rows == [("In Progress", "Success PC", world.users["Ravi"])]   # the confirming agent is the recorded actor
    # confirmation never starts a second closure event (no cascade of notifications)
    assert world.scalar("SELECT count(*) FROM fd_case_closures") == 1 and len(world.notes()) == 2   # Ravi's and Meena's own offers only


def _note_id(world):
    return world.scalar("SELECT id FROM task_notifications ORDER BY created_at LIMIT 1")


def test_confirm_twice_is_idempotent(world):
    world.client(); world.log("Ravi", "H-1B"); world.close(world.log("Priya", "H-1B"))
    with world.Session() as db, db.begin():
        assert len(cc.confirm_notification(db, _note_id(world), world.users["Ravi"], owner_resolver=world.resolver)["updated"]) == 1
    with world.Session() as db, db.begin():
        assert cc.confirm_notification(db, _note_id(world), world.users["Ravi"], owner_resolver=world.resolver) == {"state": "already_confirmed", "updated": [], "skipped": []}
    assert world.scalar("SELECT count(*) FROM fd_case_closure_links") == 1


def test_x_dismisses_for_good_and_links_nothing(world):
    world.client(); ravi = world.log("Ravi", "H-1B"); world.close(world.log("Priya", "H-1B"))
    with world.Session() as db, db.begin():
        assert cc.dismiss_notification(db, _note_id(world), world.users["Ravi"])["state"] == "dismissed"
    with world.Session() as db:
        assert cc.pending_notifications(db, world.users["Ravi"]) == []
        assert cc.inherited_closures(db, [ravi.id]) == {}
    assert world.scalar("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id=:i", i=ravi.id) is None   # X changes no status
    with world.Session() as db, db.begin():                          # a late Confirm cannot resurrect it
        assert cc.confirm_notification(db, _note_id(world), world.users["Ravi"], owner_resolver=world.resolver)["state"] == "dismissed"
    assert world.scalar("SELECT count(*) FROM fd_case_closure_links") == 0
    assert world.scalar("SELECT count(*) FROM fd_case_status_history") == 0


def test_pending_notifications_persist_in_order_and_are_private(world):
    world.client(); world.client("CL-2", "John Doe", "3125550199")
    world.log("Ravi", "H-1B"); world.log("Ravi", "Green card", client_id="CL-2")
    world.close(world.log("Priya", "H-1B")); world.close(world.log("Meena", "I-485", client_id="CL-2"))
    with world.Session() as db:
        mine = cc.pending_notifications(db, world.users["Ravi"])         # a fresh session = a new login
        assert [n["change_context"]["closing_agent_name"] for n in mine] == ["Priya", "Meena"]
        assert cc.pending_notifications(db, world.users["Gokul"]) == []   # nobody else sees them
    other = world.users["Gokul"]
    with world.Session() as db, db.begin():
        assert cc.confirm_notification(db, _note_id(world), other, owner_resolver=world.resolver)["state"] == "not_found"
        assert cc.dismiss_notification(db, _note_id(world), other)["state"] == "not_found"
    with world.Session() as db:
        assert len(cc.pending_notifications(db, world.users["Ravi"])) == 2


def test_confirm_skips_a_log_that_was_closed_meanwhile(world):
    world.client(); ravi = world.log("Ravi", "H-1B"); world.close(world.log("Priya", "H-1B"))
    world.close(ravi, "Dropped")                                       # Ravi closed his own log first
    with world.Session() as db, db.begin():
        result = cc.confirm_notification(db, _note_id(world), world.users["Ravi"], owner_resolver=world.resolver)
    assert result["state"] == "confirmed" and result["updated"] == [] and "Already Dropped" in result["skipped"][0]["reason"]
    assert world.scalar("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id=:i", i=ravi.id) == "Dropped"
    with world.Session() as db, db.begin():
        pass
    with world.Session() as db:
        assert cc.inherited_closures(db, [ravi.id]) == {}               # his own independent closure stands, no history row added
    assert world.scalar("SELECT count(*) FROM fd_case_status_history") == 0


def test_revert_reopens_linked_logs_and_notifies_confirmed_agents_only(world):
    world.client(); ravi = world.log("Ravi", "H-1B", status="Fee details shared & FU"); meena = world.log("Meena", "H-1B")
    closer = world.log("Priya", "H-1B"); world.close(closer)
    with world.Session() as db, db.begin():
        cc.confirm_notification(db, world.scalar("SELECT id FROM task_notifications WHERE recipient_id=:r", r=world.users["Ravi"]), world.users["Ravi"], owner_resolver=world.resolver)
    assert world.scalar("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id=:i", i=ravi.id) == "Success GE"
    world.close(closer, "Open")                                         # closer reopens
    assert world.scalar("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id=:i", i=ravi.id) == "Fee details shared & FU"  # put back
    with world.Session() as db:
        assert cc.inherited_closures(db, [ravi.id]) == {}
        assert cc.pending_notifications(db, world.users["Meena"]) == []  # unconfirmed one is withdrawn
        reopened = [n for n in cc.pending_notifications(db, world.users["Ravi"]) if n["notification_type"] == cc.NOTIFICATION_REVERSED]
        assert len(reopened) == 1 and "set back to their previous status" in reopened[0]["message"]
    assert world.scalar("SELECT reverted_at IS NOT NULL FROM fd_case_closures") is True
    world.close(closer, "Dropped")                                      # closing again creates a fresh event
    assert world.scalar("SELECT count(*) FROM fd_case_closures") == 2 and world.scalar("SELECT count(*) FROM fd_case_closures WHERE reverted_at IS NULL") == 1


def test_a_failed_notification_rolls_back_the_whole_closure_but_keeps_the_save(world, monkeypatch):
    world.client(); world.log("Ravi", "H-1B"); closer = world.log("Priya", "H-1B")
    monkeypatch.setattr(cc, "_json", lambda value: (_ for _ in ()).throw(RuntimeError("boom")))
    assert world.close(closer) is None
    assert world.scalar("SELECT count(*) FROM fd_case_closures") == 0 and world.notes() == []   # no half-written state
    assert world.scalar("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id=:i", i=closer.id) == "Success GE"  # save kept


def test_two_agents_closing_the_same_matter_at_once_is_deterministic(world):
    world.client(); a = world.log("Priya", "H-1B"); b = world.log("Ravi", "H-1B")
    barrier = threading.Barrier(2); results, errors = [], []

    def run(log):
        try:
            barrier.wait()
            results.append(world.close(log))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=run, args=(log,)) for log in (a, b)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert not errors
    # Both closures are real and keep their own owner; whoever came second finds the other already closed,
    # so exactly ONE notification exists, and it can never close the other agent's independently closed log.
    assert world.scalar("SELECT count(*) FROM fd_case_closures") == 2
    assert len(world.notes()) == 1
    assert world.scalar("SELECT count(DISTINCT closing_user_id) FROM fd_case_closures") == 2
    recipient = world.notes()[0]["r"]
    with world.Session() as db, db.begin():
        assert cc.confirm_notification(db, _note_id(world), recipient, owner_resolver=world.resolver)["updated"] == []


def test_a_log_has_at_most_one_active_closure_link(world):
    world.client(); ravi = world.log("Ravi", "H-1B"); world.close(world.log("Priya", "H-1B")); world.close(world.log("Meena", "H-1B"))
    for note_id in [r[0] for r in world_rows(world, "SELECT id FROM task_notifications WHERE recipient_id=:r", r=world.users["Ravi"])]:
        with world.Session() as db, db.begin():
            cc.confirm_notification(db, note_id, world.users["Ravi"], owner_resolver=world.resolver)
    assert world.scalar("SELECT count(*) FROM fd_case_closure_links WHERE analysis_id=:a AND reverted_at IS NULL", a=ravi.id) == 1


def world_rows(world, sql, **p):
    with world.Session() as db:
        return db.execute(text(sql), p).all()


def test_inherited_closure_lookup_fails_safe_when_tables_are_missing(world):
    with world.Session() as db:
        db.execute(text("DROP TABLE fd_case_closure_links CASCADE"))
        assert cc.inherited_closures(db, [1, 2]) == {}
        assert db.execute(text("SELECT 1")).scalar() == 1               # the request's transaction is still usable


def test_rollout_is_1_september_2026_onward_only(world):
    world.client()
    august = world.log("Ravi", "H-1B", day=date(2026, 8, 31))             # older log: never offered
    september = world.log("Meena", "H-1B", day=date(2026, 9, 1))          # first day covered
    result = world.close(world.log("Priya", "H-1B", day=date(2026, 10, 9)))
    assert result["notified"] == 1 and [world.names[n["r"]] for n in world.notes()] == ["Meena"]
    assert [log["analysis_id"] for log in world.notes()[0]["c"]["logs"]] == [september.id]
    assert august.id not in [log["analysis_id"] for n in world.notes() for log in n["c"]["logs"]]


def test_closing_an_august_log_notifies_nobody(world):
    world.client(); world.log("Ravi", "H-1B", day=date(2026, 10, 9))
    assert world.close(world.log("Priya", "H-1B", day=date(2026, 8, 30))) is None
    assert world.notes() == [] and world.scalar("SELECT count(*) FROM fd_case_closures") == 0


def test_start_date_can_be_overridden_and_a_bad_value_falls_back(monkeypatch):
    monkeypatch.delenv("CASE_CLOSURE_SINCE", raising=False)
    assert cc.closure_start_date() == date(2026, 9, 1)
    monkeypatch.setenv("CASE_CLOSURE_SINCE", "2026-11-01")
    assert cc.closure_start_date() == date(2026, 11, 1)
    monkeypatch.setenv("CASE_CLOSURE_SINCE", "not-a-date")
    assert cc.closure_start_date() == date(2026, 9, 1)


def test_reported_case_gokul_closes_immigration_and_shrishti_is_notified(world):
    # Same client, same matter: Gokul's log is typed "Immigration" but both logs carry "H1B/H4 EAD" in the
    # second case-type field; Shrishti's earlier log is still Open. (Real values of the reported calls.)
    world.client()
    world.log("Meena", "Immigration - H1B / H4 EAD", second_type="H1B/H4 EAD", day=date(2026, 10, 2))   # stands in for Shrishti
    closer = world.log("Gokul", "Immigration", second_type="H1B/H4 EAD", status="Fee details shared & FU", day=date(2026, 10, 7))
    assert world.close(closer, "Success PC")["notified"] == 1
    note = world.notes()[0]
    assert world.names[note["r"]] == "Meena" and note["c"]["closure_status"] == "Success PC"


def test_revert_leaves_a_status_the_agent_changed_after_confirming(world):
    world.client(); ravi = world.log("Ravi", "H-1B", status="Open"); closer = world.log("Priya", "H-1B"); world.close(closer)
    with world.Session() as db, db.begin():
        cc.confirm_notification(db, _note_id(world), world.users["Ravi"], owner_resolver=world.resolver)
    with world.Session() as db, db.begin():
        db.execute(text("UPDATE call_analysis_results SET fd_m_case_status_change='PC Follow Up' WHERE id=:i"), {"i": ravi.id})
    world.close(closer, "Open")
    assert world.scalar("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id=:i", i=ravi.id) == "PC Follow Up"


def test_confirm_refuses_a_log_that_is_not_the_recipients(world):
    world.client(); ravi = world.log("Ravi", "H-1B"); world.close(world.log("Priya", "H-1B"))
    world.owners[ravi.id] = (world.users["Gokul"], "Gokul")             # ownership changed since the notification
    with world.Session() as db, db.begin():
        result = cc.confirm_notification(db, _note_id(world), world.users["Ravi"], owner_resolver=world.resolver)
    assert result["updated"] == [] and "not assigned to you" in result["skipped"][0]["reason"]
    assert world.scalar("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id=:i", i=ravi.id) is None


# ------------------------------------------------------------------ the closer's own related logs
def _offer(world, log):
    with world.Session() as db:
        return cc.own_related_offer(db, log.call_id, world.users[log.agent], owner_resolver=world.resolver)


def _confirm_own(world, closure_id, agent, ids):
    with world.Session() as db, db.begin():
        return cc.confirm_own_related(db, closure_id, world.users[agent], ids, owner_resolver=world.resolver)


def _status(world, log):
    return world.scalar("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id=:i", i=log.id)


def test_offer_lists_only_my_open_logs_of_the_same_client_and_context(world):
    world.client(); world.client("CL-2", "Other Person", "3125559999")
    mine = world.log("Priya", "H-1B extension", status="Open")
    same = world.log("Priya", "H-1B visa", status="PC Follow Up")
    world.log("Priya", "Divorce")                                  # other context
    world.log("Priya", "H-1B", client_id="CL-2")                   # other client (phone)
    world.log("Ravi", "H-1B")                                      # same matter, another agent: never offered to Priya
    world.log("Priya", "H-1B", status="Dropped")                   # already closed
    world.log("Priya", "Immigration")                              # generic text is no context
    world.close(mine, "Success PC")
    offer = _offer(world, mine)
    assert [log["analysis_id"] for log in offer["logs"]] == [same.id]
    assert offer["logs"][0]["agent_name"] == "Priya" and offer["logs"][0]["status"] == "PC Follow Up"
    assert offer["client_name"] == "Jane Roe" and offer["client_number"] == "3125550100"
    assert offer["closure_status"] == "Success PC" and offer["case_context"] == "H-1B extension"


def test_offer_is_empty_without_related_logs_a_closure_or_for_another_agent(world):
    world.client()
    alone = world.log("Priya", "H-1B"); world.close(alone)
    assert _offer(world, alone)["logs"] == []
    open_ = world.log("Priya", "H-1B"); other = world.log("Priya", "H-1B")
    assert _offer(world, open_)["logs"] == []                      # not closed: there is no closure at all
    world.close(open_)
    assert [l["analysis_id"] for l in _offer(world, open_)["logs"]] == [other.id]
    stranger = SimpleNamespace(call_id=open_.call_id, agent="Ravi")
    assert _offer(world, stranger)["logs"] == []                   # only the closer is offered anything


def test_confirm_own_closes_with_the_closures_status_date_history_and_link(world):
    world.client()
    a = world.log("Priya", "H-1B", status="Open"); b = world.log("Priya", "H-1B visa"); keep = world.log("Priya", "Divorce")
    world.close(a, "Dropped")
    offer = _offer(world, a)
    result = _confirm_own(world, offer["closure_id"], "Priya", [log["analysis_id"] for log in offer["logs"]])
    assert result["state"] == "confirmed" and result["skipped"] == [] and result["closure_status"] == "Dropped"
    assert sorted(u["analysis_id"] for u in result["updated"]) == [b.id]
    assert _status(world, b) == "Dropped" and _status(world, keep) is None
    assert world.scalar("SELECT fd_m_date_entry FROM call_analysis_results WHERE id=:i", i=b.id) is not None
    assert result["updated"][0]["fd_m_date_entry"]
    rows = world_rows(world, "SELECT previous_status, new_status, changed_by::text FROM fd_case_status_history WHERE analysis_id=:a", a=b.id)
    assert rows == [(None, "Dropped", world.users["Priya"])]
    assert world.scalar("SELECT count(*) FROM fd_case_closure_links WHERE analysis_id=:a AND reverted_at IS NULL", a=b.id) == 1
    assert _offer(world, a)["logs"] == []                          # nothing left to offer


def test_confirm_own_sends_no_notifications_and_starts_no_second_closure(world):
    world.client()
    world.log("Ravi", "H-1B")                                      # another agent: notified once, by the original save
    a = world.log("Priya", "H-1B"); world.log("Priya", "H-1B"); world.log("Priya", "H-1B")
    world.close(a)
    before = len(world.notes())
    offer = _offer(world, a)
    _confirm_own(world, offer["closure_id"], "Priya", [l["analysis_id"] for l in offer["logs"]])
    assert before == 1 and len(world.notes()) == before
    assert world.scalar("SELECT count(*) FROM fd_case_closures") == 1


def test_confirm_own_twice_is_idempotent(world):
    world.client()
    a = world.log("Priya", "H-1B"); b = world.log("Priya", "H-1B"); world.close(a)
    offer = _offer(world, a); ids = [b.id]
    first = _confirm_own(world, offer["closure_id"], "Priya", ids)
    second = _confirm_own(world, offer["closure_id"], "Priya", ids + ids)
    assert len(first["updated"]) == 1 and second["updated"] == [] and len(second["skipped"]) == 1
    assert world.scalar("SELECT count(*) FROM fd_case_status_history WHERE analysis_id=:a", a=b.id) == 1


def test_confirm_own_revalidates_stale_logs(world):
    world.client()
    a = world.log("Priya", "H-1B"); closed = world.log("Priya", "H-1B"); moved = world.log("Priya", "H-1B")
    reassigned = world.log("Priya", "H-1B"); recontext = world.log("Priya", "H-1B"); good = world.log("Priya", "H-1B")
    world.close(a)
    offer = _offer(world, a)
    assert len(offer["logs"]) == 5
    with world.Session() as db, db.begin():                        # the matter changes while the modal is open
        db.execute(text("UPDATE call_analysis_results SET fd_m_case_status_change='Success GE' WHERE id=:i"), {"i": closed.id})
        db.execute(text("UPDATE call_analysis_results SET fd_a_case_type='Divorce' WHERE id=:i"), {"i": recontext.id})
    world.owners[reassigned.id] = (world.users["Ravi"], "Ravi")
    foreign = world.log("Ravi", "H-1B")                            # an id the agent was never shown
    result = _confirm_own(world, offer["closure_id"], "Priya",
                          [l["analysis_id"] for l in offer["logs"]] + [foreign.id])
    assert sorted(u["analysis_id"] for u in result["updated"]) == sorted([moved.id, good.id])
    assert len(result["skipped"]) == 4
    assert _status(world, closed) == "Success GE" and _status(world, recontext) is None
    assert _status(world, reassigned) is None and _status(world, foreign) is None


def test_confirm_own_refuses_another_agent_and_a_reopened_closure(world):
    world.client()
    a = world.log("Priya", "H-1B"); b = world.log("Priya", "H-1B"); world.close(a)
    offer = _offer(world, a)
    assert _confirm_own(world, offer["closure_id"], "Ravi", [b.id])["state"] == "not_found"
    assert _confirm_own(world, str(uuid.uuid4()), "Priya", [b.id])["state"] == "not_found"
    world.close(a, "Open")                                         # the closer reopens it before confirming
    result = _confirm_own(world, offer["closure_id"], "Priya", [b.id])
    assert result["state"] == "reverted" and result["updated"] == [] and _status(world, b) is None


def test_reopening_restores_the_own_logs_that_were_closed_with_it(world):
    world.client()
    a = world.log("Priya", "H-1B"); b = world.log("Priya", "H-1B", status="PC Follow Up"); world.close(a)
    offer = _offer(world, a)
    _confirm_own(world, offer["closure_id"], "Priya", [b.id])
    assert _status(world, b) == "Success GE"
    world.close(a, "Open")
    assert _status(world, b) == "PC Follow Up"


def test_more_than_a_handful_of_related_logs_are_all_offered(world):
    world.client()
    a = world.log("Priya", "H-1B"); others = [world.log("Priya", "H-1B") for _ in range(25)]; world.close(a)
    offer = _offer(world, a)
    assert len(offer["logs"]) == 25
    result = _confirm_own(world, offer["closure_id"], "Priya", [l["analysis_id"] for l in offer["logs"]])
    assert len(result["updated"]) == 25 and all(_status(world, o) == "Success GE" for o in others)


def test_concurrent_confirm_own_closes_each_log_once(world):
    world.client()
    a = world.log("Priya", "H-1B"); b = world.log("Priya", "H-1B"); world.close(a)
    offer = _offer(world, a); results = []
    def run():
        results.append(_confirm_own(world, offer["closure_id"], "Priya", [b.id]))
    threads = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sum(len(r["updated"]) for r in results) == 1
    assert world.scalar("SELECT count(*) FROM fd_case_status_history WHERE analysis_id=:a", a=b.id) == 1
