"""Leads: every agent SEES every lead; the only thing an agent may change is the Lead Category of a lead they own.

Real route + real ORM on a disposable local PostgreSQL (same setup as test_own_related_contract.py; skipped without
CASE_CLOSURE_TEST_DATABASE_URL)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from test_own_related_contract import env  # noqa: F401  (the shared disposable-database fixture)

_ADDED = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)


def _mk_lead(env, phone):
    from storage import models
    lead_id = uuid.uuid4()
    with env.Session() as db, db.begin():
        db.add(models.FDLead(id=lead_id, fd_m_leads_lead_id=f"LD#{uuid.uuid4().hex[:6]}", fd_m_leads_phone=phone, lead_added_at=_ADDED,
                             fd_m_leads_owner_id=uuid.UUID(env.users["Priyanka"]), fd_m_leads_owner="Priyanka"))
    return lead_id


def _patch(env, lead_id, field, value):
    from routes.flintdesk import leads as leads_routes
    if not any(getattr(r, "path", "") == "/api/flintdesk/leads/{lead_id}" for r in env.app.routes):
        env.app.include_router(leads_routes.router)
    return env.client.patch(f"/api/flintdesk/leads/{lead_id}", json={"field": field, "value": value})


def test_an_agent_may_edit_only_the_lead_category_of_their_own_lead(env):
    from routes.flintdesk.leads import ALL_FIELDS, FIXED_VALUES
    env.as_user("Priyanka")
    lead = _mk_lead(env, "3125550190")
    assert _patch(env, lead, "lead_category", "General Query").status_code == 200
    for field in sorted(ALL_FIELDS - {"lead_category", "owner", "lead_id"}):                # Stage 1 AND Stage 2: view only
        value = sorted(FIXED_VALUES[field])[0] if field in FIXED_VALUES else ("x@example.com" if field == "email" else "x")
        r = _patch(env, lead, field, value)
        assert r.status_code == 403 and "only edit the Lead Category of their own leads" in r.json()["detail"], (field, r.status_code)


def test_an_agent_cannot_edit_someone_elses_lead_at_all(env):
    env.as_user("Priyanka")
    mine = _mk_lead(env, "3125550191")
    env.as_user("Ravi")                                                                     # another agent: not their lead
    assert _patch(env, mine, "call_summary", "hijack").status_code == 403
    assert _patch(env, mine, "lead_category", "General Query").status_code == 403


def _list(env, **extra):
    from routes.flintdesk import leads as leads_routes
    if not any(getattr(r, "path", "") == "/api/flintdesk/leads" for r in env.app.routes):
        env.app.include_router(leads_routes.router)
    return env.client.get("/api/flintdesk/leads", params={"start_date": "2026-10-01", "end_date": "2026-10-31", **extra})


def test_every_agent_sees_every_lead_and_can_open_any_lead_card(env):
    env.as_user("Priyanka")
    first, second = _mk_lead(env, "3125550193"), _mk_lead(env, "3125550194")                # both owned by Priyanka
    env.as_user("Ravi")                                                                     # a different agent
    got = _list(env)
    assert got.status_code == 200 and {row["id"] for row in got.json()} == {str(first), str(second)}
    with env.Session() as db:
        public_id = db.execute(text("SELECT fd_m_leads_lead_id FROM fd_leads WHERE id = CAST(:i AS uuid)"), {"i": str(first)}).scalar()
    from urllib.parse import quote
    card = env.client.get(f"/api/flintdesk/leads/by-public-id/{quote(public_id, safe='')}")
    assert card.status_code == 200 and card.json()["lead_id"] == public_id
    assert {row["id"] for row in _list(env, agent="Priyanka").json()} == {str(first), str(second)}   # the shared Agent filter still narrows


def test_managers_are_unaffected_and_owner_and_lead_id_stay_protected(env):
    env.as_user("Priyanka")
    lead = _mk_lead(env, "3125550192")
    with env.Session() as db, db.begin():                                                  # the server reads the role from the database
        db.execute(text("UPDATE fd_users SET role = 'admin' WHERE id = CAST(:i AS UUID)"), {"i": env.users["Priyanka"]})
    env.who["user"] = {**env.who["user"], "role": "admin"}
    assert _patch(env, lead, "email", "boss@example.com").status_code == 200               # a manager edits Stage 1
    assert _patch(env, lead, "call_summary", "by manager").status_code == 200
    assert _patch(env, lead, "owner", "Ravi").status_code == 409                           # assignment still only via Submit
    assert _patch(env, lead, "lead_id", "LD#1").status_code == 409
