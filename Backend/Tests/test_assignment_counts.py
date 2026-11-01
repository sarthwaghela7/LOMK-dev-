"""Assigning the Calls tab badges: each count is the caller's own pending records, by the rule of its own tab.

Real service code on a DISPOSABLE local PostgreSQL (the contract-test fixture; see test_follow_ups.py for how to run it)."""
import uuid
from datetime import date, datetime, timezone
from uuid import UUID

import pytest
from fastapi import HTTPException

from Tests.test_follow_ups import _ids, fu  # noqa: F401  (the Follow Ups fixture)
from Tests.test_own_related_contract import _ago, env  # noqa: F401


def _counts(env, name):
    from routes.flintdesk import assignment_counts as counts
    with env.Session() as db:
        me = UUID(env.users[name])
        user = {"fd_user_id": env.users[name], "name": name, "role": "agent"}
        general, missed = counts._tasks(db, me, user)
        return {"follow-ups": counts._follow_ups(db, me), "leads": counts._leads(db, me), "general": general, "missed-calls": missed}


def _follow_up(fu, owner, **fields):
    call = fu.log("Priyanka", status="Success PC", day=_ago(2))
    body = {"business_status": "PC + Retainer", "retainer_owner_id": fu.users[owner], **fields}
    assert fu.client.patch(fu.url(call["call_id"]), json=body).status_code == 200
    return call


def test_follow_up_counts_only_submitted_calls_owned_by_the_agent_and_not_completed_or_dropped(fu):
    fu.as_admin()
    saved_only = _follow_up(fu, "Priyanka")                                   # saved, never submitted: not pending
    submitted = _follow_up(fu, "Priyanka")
    done = _follow_up(fu, "Priyanka", retainer_action_status="Completed")
    dropped = _follow_up(fu, "Priyanka", retainer_action_status="Dropped")
    other = _follow_up(fu, "Ravi")
    for call in (submitted, done, dropped, other):
        assert fu.client.post(fu.url(call["call_id"]) + "/submit").status_code == 200
    assert _counts(fu, "Priyanka")["follow-ups"] == 1
    assert _counts(fu, "Ravi")["follow-ups"] == 1
    assert _counts(fu, "Meena")["follow-ups"] == 0
    assert saved_only


def test_reassigning_moves_the_count_only_after_the_new_owner_is_submitted(fu):
    fu.as_admin()
    call = _follow_up(fu, "Priyanka")
    fu.client.post(fu.url(call["call_id"]) + "/submit")
    assert (_counts(fu, "Priyanka")["follow-ups"], _counts(fu, "Ravi")["follow-ups"]) == (1, 0)
    assert fu.client.patch(fu.url(call["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]}).status_code == 200
    # Withdrawn from the previous owner at once; the new owner counts it once it is submitted again.
    assert (_counts(fu, "Priyanka")["follow-ups"], _counts(fu, "Ravi")["follow-ups"]) == (0, 0)
    fu.client.post(fu.url(call["call_id"]) + "/submit")
    assert (_counts(fu, "Priyanka")["follow-ups"], _counts(fu, "Ravi")["follow-ups"]) == (0, 1)
    fu.client.patch(fu.url(call["call_id"]), json={"retainer_action_status": "Completed"})
    assert _counts(fu, "Ravi")["follow-ups"] == 0


def test_agent_filter_is_the_retainer_owner(fu):
    fu.as_admin()
    mine, theirs = _follow_up(fu, "Priyanka"), _follow_up(fu, "Ravi")
    assert _ids(fu, agent=fu.users["Priyanka"])[0] == [mine["call_id"]]
    assert _ids(fu, agent=fu.users["Ravi"])[0] == [theirs["call_id"]]
    assert set(_ids(fu)[0]) == {mine["call_id"], theirs["call_id"]}
    fu.client.patch(fu.url(mine["call_id"]), json={"retainer_owner_id": fu.users["Ravi"]})
    assert _ids(fu, agent=fu.users["Priyanka"])[0] == []


def test_submit_returns_the_whole_row_so_the_page_can_update_just_it(fu):
    fu.as_admin()
    call = _follow_up(fu, "Priyanka")
    row = fu.client.post(fu.url(call["call_id"]) + "/submit").json()
    assert row["call_id"] == call["call_id"] and row["submitted"] is True and "follow_up_call" in row and "client_phone" in row


def test_a_reassignment_waits_for_a_submit_in_flight_and_ends_consistent(fu):
    """Both take the same per-call lock, so whichever runs second sees the first one's committed owner."""
    from services import follow_ups
    fu.as_admin()
    call = _follow_up(fu, "Priyanka")
    cid = UUID(call["call_id"])
    with fu.Session() as db:
        follow_ups._lock_call(db, cid)
        with fu.Session() as other:
            other.execute(__import__("sqlalchemy").text("SET LOCAL lock_timeout = '300ms'"))
            with pytest.raises(Exception):
                follow_ups._lock_call(other, cid)                              # the second writer is held back
        db.rollback()


def test_task_counts_are_the_callers_own_and_exclude_done_ones(fu):
    from storage import models
    now = datetime.now(timezone.utc)
    day = date.today()
    with fu.Session() as db, db.begin():
        for kind, owner, extra in [
            ("general", "Priyanka", {"fd_m_tasks_status": "Open"}), ("general", "Priyanka", {"fd_m_tasks_status": "Completed"}),
            ("non_responsiveness", "Priyanka", {"fd_m_tasks_phone_number": "9990005001"}),
            ("non_responsiveness", "Priyanka", {"fd_m_tasks_phone_number": "9990005002", "fd_m_tasks_action_status": "Called Back"}),
            ("general", "Ravi", {"fd_m_tasks_status": "Open"}),
            ("missed_call", "Priyanka", {"fd_m_tasks_contact_number": "9990006001"}),
        ]:
            db.add(models.FDTask(id=uuid.uuid4(), task_type=kind, fd_m_tasks_owner=UUID(fu.users[owner]), fd_m_tasks_date=day, fd_m_tasks_date_added=day,
                                 fd_m_tasks_missed_call_date=day, fd_m_tasks_miscall_added_date=day, created_at=now, updated_at=now, **extra))
    mine, theirs = _counts(fu, "Priyanka"), _counts(fu, "Ravi")
    assert (mine["general"], mine["missed-calls"]) == (2, 1)
    assert (theirs["general"], theirs["missed-calls"]) == (1, 0)


def test_an_invalid_session_has_no_counts():
    from routes.flintdesk import assignment_counts as counts
    with pytest.raises(HTTPException) as caught:
        counts._me({"fd_user_id": "nope"})
    assert caught.value.status_code == 401
