"""Follow Ups (Assigning the Calls tab): Success PC scope, Team Lead Fills fields, Submit/assignment, access and same-day call detection.

Real routers and the real service on a DISPOSABLE local PostgreSQL (see the contract-test fixture in test_own_related_contract.py):

    CASE_CLOSURE_TEST_DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:55433/contract_test \
    DATABASE_URL=<same> SECRET_KEY=test-only pytest Tests/test_follow_ups.py
"""
import uuid
from datetime import date, datetime, time as dtime, timezone
from importlib import import_module

import pytest
from fastapi import FastAPI
from sqlalchemy import text

from Tests.test_own_related_contract import CLIENT_PHONE, SYSTEM_NUMBER, _ago, env  # noqa: F401  (the contract-test fixture and helpers)

RANGE = {"start_date": "2026-09-01", "end_date": "2026-10-31"}


@pytest.fixture
def fu(env):
    from routes.flintdesk import follow_ups as follow_up_routes
    env.app.include_router(follow_up_routes.router)
    with env.Session() as db, db.begin():
        db.execute(text(import_module("migrations.077_follow_up_retainer").STATEMENTS[0]))
    lead_id = uuid.uuid4()
    with env.Session() as db, db.begin():
        db.add(__import__("storage.models", fromlist=["FDUser"]).FDUser(id=lead_id, name="Lead", email="lead@test.local", pass_hash="x", is_active=True,
                                                                         role="team_lead", created_at=datetime.now(timezone.utc)))
    env.as_admin = lambda: env.who.update(user={"fd_user_id": str(lead_id), "name": "Lead", "role": "team_lead", "email": "lead@test.local"})
    env.url = lambda call_id="": f"/flintdesk/follow-ups{('/' + call_id) if call_id else ''}"
    return env


def _ids(env, **extra):
    got = env.client.get("/flintdesk/follow-ups", params={**RANGE, **extra})
    assert got.status_code == 200, got.text
    return [row["call_id"] for row in got.json()["items"]], got.json()


def test_only_success_pc_calls_are_listed(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    fu.log("Priyanka", status="Open", day=_ago(2)); fu.log("Ravi", status="Success GE", day=_ago(2))
    ids, body = _ids(fu)
    assert ids == [a["call_id"]] and body["can_edit"] is True


def test_team_lead_fields_validate_and_round_trip(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    ok = fu.client.patch(fu.url(a["call_id"]), json={"business_status": "PC + Retainer", "paid_amount": "1,250.5", "retainer_follow_up_required": "Yes",
                                                      "retainer_action_plan": "Call back", "retainer_action_status": "Pending", "retainer_status": "Success",
                                                      "retainer_verification": "Verified"})
    assert ok.status_code == 200, ok.text
    row = _ids(fu)[1]["items"][0]
    assert (row["business_status"], row["paid_amount"], row["retainer_action_status"], row["retainer_status"]) == ("PC + Retainer", "1250.50", "Pending", "Success")
    for bad in ({"business_status": "Retainer Only"}, {"paid_amount": "-3"}, {"paid_amount": "abc"}, {"retainer_action_status": "Done"},
                {"retainer_status": "Won"}, {"retainer_follow_up_required": "Maybe"}, {"nonsense": "x"}, {"retainer_owner_id": "not-a-uuid"}):
        assert fu.client.patch(fu.url(a["call_id"]), json=bad).status_code == 422, bad
    cleared = fu.client.patch(fu.url(a["call_id"]), json={"paid_amount": ""})
    assert cleared.status_code == 200 and cleared.json()["paid_amount"] is None


def test_a_call_that_is_not_success_pc_cannot_get_follow_up_data(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Open", day=_ago(2))
    assert fu.client.patch(fu.url(a["call_id"]), json={"business_status": "PC Only"}).status_code == 422
    assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 422
    assert fu.client.patch(fu.url(str(uuid.uuid4())), json={"business_status": "PC Only"}).status_code == 404


def test_submit_needs_an_owner_assigns_notifies_once_and_every_agent_can_see_it_read_only(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 422                       # no Retainer Owner yet
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})
    done = fu.client.post(fu.url(a["call_id"] + "/submit"))
    assert done.status_code == 200 and done.json()["submitted"] is True and done.json()["retainer_owner_name"] == "Ravi"
    assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 200                        # idempotent
    notes = fu.q("SELECT recipient_id::text, notification_type, source_page FROM task_notifications WHERE notification_type = 'follow_up_assignment'")
    assert notes == [(fu.users["Ravi"], "follow_up_assignment", "follow-ups")]                        # one notification, to the owner only
    fu.as_user("Ravi")
    ids, body = _ids(fu)
    assert ids == [a["call_id"]] and body["can_edit"] is False
    fu.as_user("Meena")
    ids, body = _ids(fu)
    assert ids == [a["call_id"]] and body["can_edit"] is False and body["items"][0]["retainer_owner_name"] == "Ravi"   # every agent sees it, view only


def test_agents_see_every_follow_up_even_a_draft_but_cannot_edit_or_submit(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})               # assigned but not submitted
    fu.as_user("Ravi")
    ids, body = _ids(fu)
    assert ids == [a["call_id"]] and body["items"][0]["submitted"] is False and body["can_edit"] is False    # agents see what managers see
    assert fu.client.patch(fu.url(a["call_id"]), json={"business_status": "PC Only"}).status_code == 403
    assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 403


def test_changing_the_owner_withdraws_the_submission(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]}); fu.client.post(fu.url(a["call_id"] + "/submit"))
    moved = fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Meena"]})
    assert moved.json()["submitted"] is False and moved.json()["retainer_owner_name"] == "Meena"
    fu.client.post(fu.url(a["call_id"] + "/submit"))
    owners = {r[0] for r in fu.q("SELECT recipient_id::text FROM task_notifications WHERE notification_type = 'follow_up_assignment'")}
    assert owners == {fu.users["Ravi"], fu.users["Meena"]}


def test_same_day_follow_up_call_is_detected_only_for_the_owner_the_client_and_the_day(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(0))                                           # the original call: client CLIENT_PHONE
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]}); fu.client.post(fu.url(a["call_id"] + "/submit"))
    today = datetime.now().astimezone(__import__("zoneinfo").ZoneInfo("America/Chicago")).date()

    def made(extension, number, day):
        with fu.Session() as db, db.begin():
            db.execute(text("INSERT INTO calls (call_id, src, dst, disposition, extension, call_date, call_time, call_reference) "
                            "VALUES (CAST(:c AS uuid), :s, :d, 'ANSWERED', :e, :day, '15:00', :r)"),
                       {"c": str(uuid.uuid4()), "s": SYSTEM_NUMBER, "d": number, "e": extension, "day": day, "r": "AF#" + uuid.uuid4().hex[:5]})

    def follow_up():
        return _ids(fu)[1]["items"][0]["follow_up_call"]

    assert follow_up() is None                                                                         # nothing yet
    made("101", CLIENT_PHONE, today)                                                                   # Priyanka's extension: not the owner
    made("102", "3125557777", today)                                                                   # the owner, another client
    made("102", CLIENT_PHONE, today.replace(day=max(1, today.day - 3)))                                # the owner and client, another day
    assert follow_up() is None
    made("102", "(312) 555-0100", today)                                                               # the owner, the client (formatted), same day
    assert follow_up() is not None and follow_up()["call_reference"].startswith("AF#")


def _add_user(fu, name, role, active=True):
    from storage.models import FDUser
    uid = uuid.uuid4()
    with fu.Session() as db, db.begin():
        db.add(FDUser(id=uid, name=name, email=f"{name.lower()}@test.local", pass_hash="x", is_active=active, role=role, created_at=datetime.now(timezone.utc)))
    return str(uid)


def _set_user(fu, user_id, **columns):
    with fu.Session() as db, db.begin():
        db.execute(text("UPDATE fd_users SET " + ", ".join(f"{column} = :{column}" for column in columns) + " WHERE id = CAST(:id AS uuid)"), {"id": user_id, **columns})


def test_retainer_owner_must_be_an_active_agent(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    ok = fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})
    assert ok.status_code == 200 and ok.json()["retainer_owner_name"] == "Ravi"
    for name, role, active in (("Boss", "admin", True), ("Tlead", "team_lead", True), ("Expert", "subject_matter_expert", True),
                               ("Sme", "sme", True), ("Gone", "agent", False)):
        other = _add_user(fu, name, role, active)
        got = fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": other})
        assert got.status_code == 422, (role, active)
        assert "Retainer Owner must be an active Front Desk Agent" in got.json()["detail"]
    assert fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": str(uuid.uuid4())}).status_code == 422      # no such user
    assert _ids(fu)[1]["items"][0]["retainer_owner_id"] == fu.users["Ravi"]                                           # the rejected attempts changed nothing


def test_submit_rechecks_that_the_owner_is_still_an_active_agent(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})
    _set_user(fu, fu.users["Ravi"], is_active=False)
    inactive = fu.client.post(fu.url(a["call_id"] + "/submit"))
    assert inactive.status_code == 422 and "active Front Desk Agent" in inactive.json()["detail"]
    _set_user(fu, fu.users["Ravi"], is_active=True, role="team_lead")
    assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 422
    assert fu.q("SELECT submitted_at FROM fd_follow_up_retainer") == [(None,)]
    assert fu.q("SELECT count(*) FROM task_notifications WHERE notification_type = 'follow_up_assignment'") == [(0,)]
    _set_user(fu, fu.users["Ravi"], role="agent")
    done = fu.client.post(fu.url(a["call_id"] + "/submit"))
    assert done.status_code == 200 and done.json()["submitted"] is True


def _notes(fu):
    return fu.q("SELECT recipient_id::text FROM task_notifications WHERE notification_type = 'follow_up_assignment'")


def test_saving_the_retainer_owner_is_only_a_draft(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    saved = fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})                    # saving again changes nothing
    fu.client.patch(fu.url(a["call_id"]), json={"business_status": "PC Only", "paid_amount": "10"})
    body = saved.json()
    assert body["retainer_owner_name"] == "Ravi" and body["submitted"] is False and body["assigned_at"] is None   # the draft is kept, not active
    assert _notes(fu) == []
    assert fu.q("SELECT submitted_at, assigned_at, submitted_by FROM fd_follow_up_retainer") == [(None, None, None)]
    fu.as_user("Ravi")
    item = _ids(fu)[1]["items"][0]                                                                        # agents see the draft too, read only...
    assert item["submitted"] is False and item["retainer_owner_name"] == "Ravi" and item["assigned_at"] is None   # ...but it is not an assignment
    fu.as_admin()
    assert _ids(fu)[1]["items"][0]["retainer_owner_name"] == "Ravi"                                       # managers still see the draft


def test_only_the_owner_is_notified_on_submit_while_every_agent_sees_the_follow_up(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})
    fu.as_user("Ravi"); assert _ids(fu)[0] == [a["call_id"]] and _notes(fu) == []                          # visible as a draft, nobody notified
    fu.as_admin()
    done = fu.client.post(fu.url(a["call_id"] + "/submit"))
    assert done.status_code == 200 and done.json()["submitted"] is True and done.json()["assigned_at"]
    assert _notes(fu) == [(fu.users["Ravi"],)]
    fu.as_user("Ravi"); assert _ids(fu)[0] == [a["call_id"]]
    fu.as_user("Meena"); assert _ids(fu)[1]["items"][0]["submitted"] is True                               # everyone else sees it submitted, read only


def test_changing_the_draft_owner_before_submit_notifies_only_the_final_owner(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Meena"]})
    assert _notes(fu) == []
    assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 200
    assert _notes(fu) == [(fu.users["Meena"],)]
    fu.as_user("Ravi"); assert _ids(fu)[1]["items"][0]["retainer_owner_name"] == "Meena"                    # both agents see it; it is Meena's
    fu.as_user("Meena"); assert _ids(fu)[0] == [a["call_id"]]


def test_a_failed_submit_changes_and_notifies_nothing(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 422                            # no owner chosen
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})
    _set_user(fu, fu.users["Ravi"], is_active=False)
    assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 422                            # owner no longer active
    _set_user(fu, fu.users["Ravi"], is_active=True, role="admin")
    assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 422                            # owner no longer an agent
    assert fu.q("SELECT submitted_at, assigned_at, submitted_by FROM fd_follow_up_retainer") == [(None, None, None)]
    assert _notes(fu) == []


def test_a_failure_while_notifying_rolls_the_submission_back(fu, monkeypatch):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})
    import services.flintdesk_notifications as notifications

    def boom(*args, **kwargs):
        raise RuntimeError("notification store unavailable")
    monkeypatch.setattr(notifications, "add_notifications", boom)
    with pytest.raises(RuntimeError):
        fu.client.post(fu.url(a["call_id"] + "/submit"))
    assert fu.q("SELECT submitted_at, assigned_at FROM fd_follow_up_retainer") == [(None, None)] and _notes(fu) == []
    monkeypatch.undo()
    assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 200 and _notes(fu) == [(fu.users["Ravi"],)]


def test_repeated_submit_never_duplicates_the_notification(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})
    first = fu.client.post(fu.url(a["call_id"] + "/submit")).json()
    for _ in range(3):
        again = fu.client.post(fu.url(a["call_id"] + "/submit"))
        assert again.status_code == 200 and again.json()["submitted_at"] == first["submitted_at"]
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_action_plan": "Call back"})                     # editing other fields keeps it submitted
    assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 200
    assert _notes(fu) == [(fu.users["Ravi"],)]


@pytest.mark.parametrize("role", ["admin", "team_lead", "subject_matter_expert", "sme"])
def test_admin_team_lead_and_sme_can_save_submit_and_see_everything(fu, role):
    manager = _add_user(fu, f"M{role[:3]}", role)
    fu.who.update(user={"fd_user_id": manager, "name": "Manager", "role": role, "email": "m@test.local"})
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    assert fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]}).status_code == 200
    ids, body = _ids(fu)
    assert ids == [a["call_id"]] and body["can_edit"] is True                                             # a draft is visible to managers
    assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 200


def test_the_shared_agent_filter_still_narrows_the_managers_list(fu):
    fu.as_admin()
    mine, other = fu.log("Priyanka", status="Success PC", day=_ago(2)), fu.log("Ravi", status="Success PC", day=_ago(2))
    assert set(_ids(fu)[0]) == {mine["call_id"], other["call_id"]}
    assert _ids(fu, agent="Priyanka")[0] == [mine["call_id"]]


def test_everything_works_when_call_analysis_results_call_id_is_a_uuid_column(fu):
    """Production stores call_analysis_results.call_id as uuid (the model and older schemas say text); comparing it with text failed there."""
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    with fu.Session() as db, db.begin():
        db.execute(text("ALTER TABLE call_analysis_results ALTER COLUMN call_id TYPE uuid USING call_id::uuid"))
    try:
        assert _ids(fu)[0] == [a["call_id"]]
        assert fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]}).status_code == 200
        assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 200
        fu.as_user("Ravi")
        assert _ids(fu)[0] == [a["call_id"]]
    finally:
        with fu.Session() as db, db.begin():
            db.execute(text("ALTER TABLE call_analysis_results ALTER COLUMN call_id TYPE text USING call_id::text"))


def _notes_of(fu, kind):
    return [r[0] for r in fu.q("SELECT recipient_id::text FROM task_notifications WHERE notification_type = :k ORDER BY created_at", k=kind)]


def test_reassigning_a_submitted_follow_up_tells_the_previous_owner_it_is_no_longer_theirs(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})
    fu.client.post(fu.url(a["call_id"] + "/submit"))
    assert _notes_of(fu, "follow_up_unassigned") == []
    moved = fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Meena"]})
    assert moved.status_code == 200 and moved.json()["submitted"] is False
    assert _notes_of(fu, "follow_up_unassigned") == [fu.users["Ravi"]]                                  # the old owner is told, nobody else
    note = fu.q("SELECT title, message, source_page FROM task_notifications WHERE notification_type = 'follow_up_unassigned'")[0]
    assert note[2] == "follow-ups" and "no longer assigned to you" in note[1].lower() and note[0] == "Follow up no longer assigned to you"
    fu.as_user("Ravi"); item = _ids(fu)[1]["items"][0]                                                   # everyone still sees the call, but it is Meena's now
    assert item["retainer_owner_name"] == "Meena" and item["submitted"] is False
    fu.as_admin()
    assert fu.client.post(fu.url(a["call_id"] + "/submit")).status_code == 200
    assert _notes_of(fu, "follow_up_assignment") == [fu.users["Ravi"], fu.users["Meena"]]               # the new owner is told when Submit is pressed
    assert _notes_of(fu, "follow_up_unassigned") == [fu.users["Ravi"]]


def test_unassigned_notice_only_when_a_submitted_call_actually_changes_hands(fu):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Meena"]})                # still a draft: nobody was assigned yet
    assert _notes_of(fu, "follow_up_unassigned") == []
    fu.client.post(fu.url(a["call_id"] + "/submit"))
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Meena"]})                # same owner again
    fu.client.patch(fu.url(a["call_id"]), json={"business_status": "PC Only", "paid_amount": "5"})      # other fields
    assert _notes_of(fu, "follow_up_unassigned") == [] and fu.q("SELECT submitted_at IS NOT NULL FROM fd_follow_up_retainer") == [(True,)]
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Meena"]})
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": ""})                               # taken away from everyone
    assert _notes_of(fu, "follow_up_unassigned") == [fu.users["Meena"]]
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})                 # a draft again: no second notice
    assert _notes_of(fu, "follow_up_unassigned") == [fu.users["Meena"]]


def test_a_failure_while_notifying_the_previous_owner_keeps_the_original_assignment(fu, monkeypatch):
    fu.as_admin()
    a = fu.log("Priyanka", status="Success PC", day=_ago(2))
    fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})
    fu.client.post(fu.url(a["call_id"] + "/submit"))
    import services.flintdesk_notifications as notifications

    def boom(*args, **kwargs):
        raise RuntimeError("notification store unavailable")
    monkeypatch.setattr(notifications, "add_notifications", boom)
    with pytest.raises(RuntimeError):
        fu.client.patch(fu.url(a["call_id"]), json={"retainer_owner_id": fu.users["Meena"]})
    monkeypatch.undo()
    assert fu.q("SELECT retainer_owner_id::text, submitted_at IS NOT NULL FROM fd_follow_up_retainer") == [(fu.users["Ravi"], True)]
    fu.as_user("Ravi"); assert _ids(fu)[0] == [a["call_id"]]                                            # nothing changed: Ravi still has it


def test_an_agent_can_use_the_shared_agent_filter_on_the_list(fu):
    fu.as_admin()
    mine, other = fu.log("Priyanka", status="Success PC", day=_ago(2)), fu.log("Ravi", status="Success PC", day=_ago(2))
    fu.as_user("Meena")
    assert set(_ids(fu)[0]) == {mine["call_id"], other["call_id"]}
    assert _ids(fu, agent="Priyanka")[0] == [mine["call_id"]]
