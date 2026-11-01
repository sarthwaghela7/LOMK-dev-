"""Assigning the Calls: every signed-in user SEES every task (General, Non Responsive, Missed Call); only editors may change any.

Real routes + real ORM on a disposable local PostgreSQL (same fixture as test_own_related_contract.py)."""
import uuid
from datetime import date, datetime, timezone

from test_own_related_contract import env  # noqa: F401  (the shared disposable-database fixture)

DAY = date(2026, 10, 20)


def _tasks_api(env):
    from routes.flintdesk import tasks as task_routes
    if not any(getattr(r, "path", "") == "/api/flintdesk/tasks" for r in env.app.routes):
        env.app.include_router(task_routes.router)


def _task(env, task_type, owner, submitted, **extra):
    from storage import models
    task_id = uuid.uuid4()
    with env.Session() as db, db.begin():
        db.add(models.FDTask(id=task_id, task_type=task_type, fd_m_tasks_date=DAY, fd_m_tasks_missed_call_date=DAY, fd_m_tasks_date_added=DAY,
                             fd_m_tasks_owner=uuid.UUID(env.users[owner]) if owner else None,
                             submitted_at=datetime.now(timezone.utc) if submitted else None, created_at=datetime.now(timezone.utc),
                             updated_at=datetime.now(timezone.utc), **extra))
    return str(task_id)


def _list(env, **params):
    return env.client.get("/api/flintdesk/tasks", params={"start_date": "2026-10-01", "end_date": "2026-10-31", **params})


def test_every_agent_sees_every_task_of_every_type_submitted_or_not(env):
    _tasks_api(env)
    general = _task(env, "general", "Priyanka", True, fd_m_tasks_tasks="Call back")
    non_responsive = _task(env, "non_responsiveness", "Meena", False)           # not even submitted yet
    missed = _task(env, "missed_call", None, False)
    env.as_user("Ravi")                                                         # owns none of them
    got = _list(env)
    assert got.status_code == 200 and {row["id"] for row in got.json()} == {general, non_responsive, missed}
    assert {row["id"] for row in _list(env, task_type="non_responsiveness").json()} == {non_responsive}
    mine = _list(env, agent=env.users["Priyanka"])                              # the shared Agent filter still narrows
    assert {row["id"] for row in mine.json()} == {general}


def test_agents_cannot_create_change_or_assign_any_task(env):
    _tasks_api(env)
    general = _task(env, "general", "Priyanka", True, fd_m_tasks_tasks="Call back")
    missed = _task(env, "missed_call", None, False)
    env.as_user("Ravi")
    for task_type in ("general", "non_responsiveness", "missed_call"):                 # no self-submitting of call tasks any more
        made = env.client.post("/api/flintdesk/tasks", json={"task_type": task_type, "data": {"name": "x"}, "submit": False})
        assert made.status_code == 403, task_type
    assert env.client.patch(f"/api/flintdesk/tasks/{general}", json={"task_type": "general", "data": {"comments": "x"}, "submit": False}).status_code == 403
    assert env.client.request("DELETE", "/api/flintdesk/tasks/bulk", json={"task_type": "missed_call", "ids": [missed]}).status_code == 403
    assert env.client.post("/api/flintdesk/tasks/assign", json={"task_type": "missed_call", "ids": [missed], "owner_id": env.users["Ravi"]}).status_code == 403
    assert _list(env).status_code == 200 and len(_list(env).json()) == 2                 # and nothing was changed or removed
