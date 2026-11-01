"""Who may read and who may change prompts. Real routers and the real role checks; only the prompt store is replaced by a recorder, so a
refused request can be shown to have never reached it."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.auth import get_current_user
from routes import prompts as legacy_prompts
from routes.flintdesk import fd_prompts

ADMINS = ["admin", "superuser", "super_user"]
VIEWERS = ["team_lead", "Team Lead", "subject_matter_expert", "sme"]
OTHERS = ["agent", "", "viewer"]
ACTIVE = {"prompt_key": "follow_up_date", "prompt_name": "Follow-Up Date", "system_prompt": "S", "user_prompt_template": "U", "version": 3,
          "is_active": True, "updated_at": None, "updated_by": "admin"}
OLDER = {**ACTIVE, "version": 2, "is_active": False}


class Store:
    def __init__(self):
        self.saved, self.activated = [], []

    def list_prompts(self, prompt_key=None):
        return [ACTIVE, OLDER, {**ACTIVE, "prompt_key": "priority_case_classification"}]

    def save_new_version(self, key, system, user, actor, prompt_name=None):
        self.saved.append((key, system, user, actor))
        return 4

    def activate_version(self, key, version, actor):
        self.activated.append((key, version, actor))
        return version in (1, 2)


@pytest.fixture
def api(monkeypatch):
    store = Store()
    monkeypatch.setattr(fd_prompts, "list_prompts", store.list_prompts)
    monkeypatch.setattr(fd_prompts, "save_new_version", store.save_new_version)
    monkeypatch.setattr(fd_prompts, "activate_version", store.activate_version)
    monkeypatch.setattr(fd_prompts, "audit", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(fd_prompts.router)
    app.include_router(legacy_prompts.router)
    who = {"user": None}
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    client = TestClient(app)
    client.store = store
    client.as_role = lambda role: who.update(user={"role": role, "name": "Tester", "fd_user_id": "u1"})
    return client


BODY = {"system_prompt": "New system prompt", "user_prompt_template": "New template {{x}}"}


@pytest.mark.parametrize("role", ADMINS + VIEWERS)
def test_admins_team_leads_and_smes_can_read_the_prompts(api, role):
    api.as_role(role)
    got = api.get("/flintdesk/fd-prompts?include_history=true")
    assert got.status_code == 200
    body = got.json()
    assert [row["version"] for row in body["active"]] == [3] and [row["version"] for row in body["history"]] == [3, 2]
    assert {row["prompt_key"] for row in body["active"]} <= set(body["keys"])           # only this panel's prompts, never the Priority one


@pytest.mark.parametrize("role", OTHERS)
def test_other_roles_cannot_read_the_prompts(api, role):
    api.as_role(role)
    assert api.get("/flintdesk/fd-prompts").status_code == 403


@pytest.mark.parametrize("role", ADMINS)
def test_an_admin_can_save_a_new_version_and_restore_an_earlier_one(api, role):
    api.as_role(role)
    saved = api.put("/flintdesk/fd-prompts/follow_up_date", json=BODY)
    assert saved.status_code == 200 and saved.json() == {"prompt_key": "follow_up_date", "version": 4}
    assert api.store.saved == [("follow_up_date", BODY["system_prompt"], BODY["user_prompt_template"], "Tester")]
    assert api.post("/flintdesk/fd-prompts/follow_up_date/rollback/2").status_code == 200
    assert api.store.activated == [("follow_up_date", 2, "Tester")]
    assert api.post("/flintdesk/fd-prompts/follow_up_date/rollback/99").status_code == 404      # an unknown version changes nothing


@pytest.mark.parametrize("role", VIEWERS + OTHERS)
def test_team_leads_smes_and_others_get_403_on_every_prompt_change(api, role):
    api.as_role(role)
    assert api.put("/flintdesk/fd-prompts/follow_up_date", json=BODY).status_code == 403
    assert api.post("/flintdesk/fd-prompts/follow_up_date/rollback/2").status_code == 403
    assert api.store.saved == [] and api.store.activated == []                                 # the refused requests never reached the store
    # the older prompts-metadata API: reading is open to any signed-in user, but every change is for a superuser only
    assert api.post("/prompts", json={"key": "x"}).status_code == 403
    assert api.put("/prompts/1", json={"key": "y"}).status_code == 403
    assert api.delete("/prompts/1").status_code == 403


def test_anonymous_requests_are_refused(monkeypatch):
    app = FastAPI()
    app.include_router(fd_prompts.router)
    client = TestClient(app)
    assert client.get("/flintdesk/fd-prompts").status_code == 401
    assert client.put("/flintdesk/fd-prompts/follow_up_date", json=BODY).status_code == 401
    assert client.post("/flintdesk/fd-prompts/follow_up_date/rollback/1").status_code == 401


def test_admin_input_is_validated_and_other_prompts_are_not_reachable(api):
    api.as_role("admin")
    assert api.put("/flintdesk/fd-prompts/priority_case_classification", json=BODY).status_code == 404   # not a Stage 2 prompt: untouched
    assert api.put("/flintdesk/fd-prompts/follow_up_date", json={"system_prompt": "x" * 20001, "user_prompt_template": "u"}).status_code == 422
    assert api.put("/flintdesk/fd-prompts/follow_up_date", json={"system_prompt": "only one field"}).status_code == 422
    assert api.store.saved == []
