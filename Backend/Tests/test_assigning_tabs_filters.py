"""Assigning the Calls tabs: every server-side filter of SM Leads and Call Tasks / Missed Calls returns the right rows.

Real routes + real ORM on a disposable local PostgreSQL (same fixture as test_own_related_contract.py)."""
import uuid
from datetime import date, datetime, time as dtime, timezone
from importlib import import_module

from test_own_related_contract import SYSTEM_NUMBER, env  # noqa: F401  (the shared disposable-database fixture)


def _sm_api(env, monkeypatch):
    for name in ("migrations.061_create_sm_leads", "migrations.062_sm_leads_derived_completion"):
        module = import_module(name)
        monkeypatch.setattr(module, "engine", env.engine)
        getattr(module, [n for n in dir(module) if n.startswith("migrate_")][0])()
    from routes.flintdesk import sm_leads
    if not any(getattr(r, "path", "") == "/api/flintdesk/sm-leads" for r in env.app.routes):
        env.app.include_router(sm_leads.router)


def _sm(env, number, phone, status, owner, day, **extra):
    from storage import models
    row_id = uuid.uuid4()
    digits = "".join(ch for ch in phone if ch.isdigit())
    with env.Session() as db, db.begin():
        db.add(models.FDSMLead(id=row_id, sm_lead_number=number, phone=phone, phone_digits=digits, status=status,
                               owner_id=uuid.UUID(env.users[owner]) if owner else None, owner=owner,
                               date_added=datetime(2026, 10, day, 15, tzinfo=timezone.utc), **extra))
    return f"SM#{number}"


def _sm_ids(env, **params):
    got = env.client.get("/api/flintdesk/sm-leads", params=params)
    assert got.status_code == 200, got.text
    return sorted(item["sm_lead_id"] for item in got.json()["items"])


def test_every_sm_leads_filter_returns_the_right_rows(env, monkeypatch):
    _sm_api(env, monkeypatch)
    from storage import models
    env.as_user("Ravi")
    _sm(env, 1, "(312) 555-0101", "Pending", None, 20)
    _sm(env, 2, "312-555-0202", "Assigned", "Priyanka", 21)
    _sm(env, 3, "+1 999 888 7777", "Assigned", "Priyanka", 22)
    _sm(env, 4, "312 555 0404", "Assigned", "Meena", 22)
    # An outbound call from FlintDesk's number to SM#3's client on the day it was added makes it Completed.
    with env.Session() as db, db.begin():
        db.add(models.Call(call_id=uuid.uuid4(), call_date=date(2026, 10, 22), call_time=dtime(10, 0),
                           src=SYSTEM_NUMBER, dst="9998887777"))
    assert _sm_ids(env) == ["SM#1", "SM#2", "SM#3", "SM#4"]
    assert _sm_ids(env, start_date="2026-10-22", end_date="2026-10-22") == ["SM#3", "SM#4"]
    assert _sm_ids(env, start_date="2026-10-21") == ["SM#2", "SM#3", "SM#4"]
    assert _sm_ids(env, end_date="2026-10-20") == ["SM#1"]
    assert _sm_ids(env, status="Pending") == ["SM#1"]
    assert _sm_ids(env, status="Assigned") == ["SM#2", "SM#4"]
    assert _sm_ids(env, status="Completed") == ["SM#3"]
    assert _sm_ids(env, phone="(312) 555-0202") == ["SM#2"]
    assert _sm_ids(env, phone="555-0404") == ["SM#4"]
    assert _sm_ids(env, phone="+1 999 888 7777") == ["SM#3"]
    assert _sm_ids(env, sm_lead_id="SM#3") == ["SM#3"]
    assert _sm_ids(env, sm_lead_id="sm 2") == ["SM#2"]
    assert _sm_ids(env, sm_lead_id="4") == ["SM#4"]
    assert _sm_ids(env, owner_id=env.users["Priyanka"]) == ["SM#2", "SM#3"]
    assert _sm_ids(env, owner_id=env.users["Meena"], status="Assigned") == ["SM#4"]
    assert _sm_ids(env, owner_id=env.users["Meena"], status="Pending") == []
    assert env.client.get("/api/flintdesk/sm-leads", params={"status": "Bogus"}).status_code == 400


def _tasks_api(env):
    from routes.flintdesk import tasks as task_routes
    if not any(getattr(r, "path", "") == "/api/flintdesk/tasks" for r in env.app.routes):
        env.app.include_router(task_routes.router)


def _task(env, task_type, owner, day, **extra):
    from storage import models
    task_id = uuid.uuid4()
    with env.Session() as db, db.begin():
        db.add(models.FDTask(id=task_id, task_type=task_type, fd_m_tasks_date=day, fd_m_tasks_date_added=day,
                             fd_m_tasks_missed_call_date=day if task_type == "missed_call" else None,
                             fd_m_tasks_owner=uuid.UUID(env.users[owner]) if owner else None,
                             created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc), **extra))
    return str(task_id)


def _task_ids(env, **params):
    got = env.client.get("/api/flintdesk/tasks", params=params)
    assert got.status_code == 200, got.text
    return {row["id"] for row in got.json()}


def test_every_call_tasks_and_missed_calls_filter_returns_the_right_rows(env):
    _tasks_api(env)
    env.as_user("Ravi")
    old_general = _task(env, "general", "Priyanka", date(2026, 10, 5), fd_m_tasks_tasks="Old")
    general = _task(env, "general", "Priyanka", date(2026, 10, 22), fd_m_tasks_tasks="Call back")
    non_responsive = _task(env, "non_responsiveness", "Meena", date(2026, 10, 22), fd_m_tasks_phone_number="3125550101")
    missed_mine = _task(env, "missed_call", "Priyanka", date(2026, 10, 22), fd_m_tasks_contact_number="3125550102")
    missed_other = _task(env, "missed_call", "Meena", date(2026, 10, 25), fd_m_tasks_contact_number="3125550103")
    missed_unowned = _task(env, "missed_call", None, date(2026, 10, 22), fd_m_tasks_contact_number="3125550104")
    everything = {old_general, general, non_responsive, missed_mine, missed_other, missed_unowned}
    assert _task_ids(env) == everything
    # date range (Call Tasks and Missed Calls both send it)
    assert _task_ids(env, start_date="2026-10-22", end_date="2026-10-22") == {general, non_responsive, missed_mine, missed_unowned}
    assert _task_ids(env, start_date="2026-10-23") == {missed_other}
    assert _task_ids(env, end_date="2026-10-10") == {old_general}
    assert _task_ids(env, start_date="2026-10-22", end_date="2026-10-25") == everything - {old_general}
    # task type (the Missed Calls tab asks for missed_call only)
    assert _task_ids(env, task_type="missed_call") == {missed_mine, missed_other, missed_unowned}
    assert _task_ids(env, task_type="non_responsiveness") == {non_responsive}
    # shared Agent filter
    assert _task_ids(env, agent=env.users["Priyanka"]) == {old_general, general, missed_mine}
    assert _task_ids(env, agent=env.users["Meena"], task_type="missed_call") == {missed_other}
    # combinations
    assert _task_ids(env, agent=env.users["Priyanka"], task_type="missed_call", start_date="2026-10-22", end_date="2026-10-22") == {missed_mine}
    assert _task_ids(env, agent=env.users["Meena"], start_date="2026-10-01", end_date="2026-10-21") == set()
    assert env.client.get("/api/flintdesk/tasks", params={"agent": "not-an-id"}).status_code == 422
