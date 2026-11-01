from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from routes.flintdesk import call_updates
from services import call_updates as service

TODAY = call_updates._today_central()


class _Db:
    def __init__(self, user=None, existing=None, users=()):
        self.user, self.existing, self.added, self.commits = user, existing, [], 0
        self.users = {u.id: u for u in (user, *users) if u is not None}

    def get(self, model, key):
        return self.users.get(key)

    def query(self, *args):
        return self

    def filter(self, *args):
        return self

    def first(self):
        return self.existing

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1


def _agent(role="agent", active=True):
    return SimpleNamespace(id=uuid4(), role=role, is_active=active)


def _entry(agent_id, **overrides):
    values = dict(entry_date=TODAY, agent_id=agent_id, metric="wa_answered", value=5, note=None)
    values.update(overrides)
    return call_updates.ManualEntry(**values)


def _call(entry, agent, db, role="agent"):
    return call_updates.save_manual_entry(entry, user={"fd_user_id": str(agent.id), "role": role}, db=db)


def test_agent_can_save_their_own_value():
    agent = _agent()
    db = _Db(user=agent)
    result = _call(_entry(agent.id), agent, db)
    assert result["value"] == 5 and result["metric"] == "wa_answered"
    assert db.commits == 1 and len(db.added) == 1


def test_updating_an_existing_entry_does_not_add_a_row():
    agent = _agent()
    existing = SimpleNamespace(value=1, note=None, updated_by=None, updated_at=None)
    db = _Db(user=agent, existing=existing)
    result = _call(_entry(agent.id, value=9), agent, db)
    assert existing.value == 9 and result["value"] == 9 and not db.added


def test_an_agent_cannot_edit_another_agents_entry():
    me, other = _agent(), _agent()
    with pytest.raises(HTTPException) as exc:
        _call(_entry(other.id), me, _Db(user=me, users=[other]))
    assert exc.value.status_code == 403


@pytest.mark.parametrize("role", ["admin", "team_lead", "subject_matter_expert"])
def test_managers_can_edit_any_agents_entry(role):
    manager, agent = _agent(role=role), _agent()
    db = _Db(user=manager, users=[agent])
    result = _call(_entry(agent.id, value=3), manager, db, role=role)
    assert result["agent_id"] == str(agent.id) and result["value"] == 3
    assert db.added[0].updated_by == manager.id


@pytest.mark.parametrize("role", ["admin", "team_lead", "subject_matter_expert"])
def test_values_can_only_be_entered_for_front_desk_agents(role):
    manager, other_manager = _agent(role=role), _agent(role="admin")
    with pytest.raises(HTTPException) as exc:
        _call(_entry(other_manager.id), manager, _Db(user=manager, users=[other_manager]), role=role)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        _call(_entry(uuid4()), manager, _Db(user=manager), role=role)
    assert exc.value.status_code == 403


def test_future_dates_and_stray_notes_are_rejected():
    agent = _agent()
    with pytest.raises(HTTPException) as exc:
        _call(_entry(agent.id, entry_date=TODAY + timedelta(days=1)), agent, _Db(user=agent))
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        _call(_entry(agent.id, note="hello"), agent, _Db(user=agent))
    assert exc.value.status_code == 422


def test_initial_emails_keep_their_note():
    agent = _agent()
    db = _Db(user=agent)
    result = _call(_entry(agent.id, metric="initial_emails", value=4, note="  Seminar follow-ups  "), agent, db)
    assert result["note"] == "Seminar follow-ups"


def test_validation_rejects_negative_and_huge_numbers():
    with pytest.raises(Exception):
        _entry(uuid4(), value=-1)
    with pytest.raises(Exception):
        _entry(uuid4(), value=100001)
    with pytest.raises(Exception):
        _entry(uuid4(), metric="incoming")


def test_missed_calls_are_credited_to_the_called_back_agent_only():
    tasks = [
        {"task_type": "missed_call", "data": {"callback": {"agent": "Gokul", "called_back_date": "2026-10-01"}}},
        {"task_type": "missed_call", "data": {"callback": {"agent": "Gokul", "called_back_date": "2026-10-01"}}},
        {"task_type": "missed_call", "data": {"callback": {"agent": "Priyanka", "called_back_date": "2026-10-02"}}},
        {"task_type": "missed_call", "data": {"callback": {"agent": "-", "called_back_date": "2026-10-01"}}},
        {"task_type": "missed_call", "data": {}},  # not called back: credited to nobody
        {"task_type": "non_responsiveness", "data": {"callback": {"agent": "Gokul", "called_back_date": "2026-10-01"}}},
    ]
    assert dict(service.missed_call_counts(tasks)) == {("2026-10-01", "Gokul"): 2, ("2026-10-02", "Priyanka"): 1}
