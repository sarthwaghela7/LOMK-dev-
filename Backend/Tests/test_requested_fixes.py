import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from middleware.auth import get_current_user
from routes import fd_auth
from routes.flintdesk import sync_filters
from services.Processing_pipeline.fd_llm_node import FDLLM
from storage.db import get_db
from utils.jwt import create_access_token, verify_token


@pytest.mark.parametrize("duration,detailed", [(60, False), (180, False), (180.01, True), (650, True)])
def test_generation_uses_real_duration_and_validates_name(monkeypatch, duration, detailed):
    node = FDLLM()
    monkeypatch.setattr(node, "audio_duration", lambda call: duration)
    sent = {}
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": json.dumps({
        "fd_a_client_name": "  MARY   KENNEDY ", "fd_a_call_summary": "Client requested advice."
    })}}]}
    def post(*args, **kwargs):
        sent.update(kwargs)
        return response
    monkeypatch.setattr("services.Processing_pipeline.fd_llm_node.requests.post", post)
    result = node.generate_flintdesk_analysis("Mary Kennedy: I am the agent. Caller: I need advice.", SimpleNamespace(billsec=9999))
    prompt = sent["json"]["messages"][0]["content"]
    assert ("LONG CALL:" in prompt) is detailed
    assert result["fd_a_client_name"] is None
    assert result["fd_a_call_summary"] == "Client requested advice."


@pytest.mark.parametrize("name", ["Gokul", " SATISH ", "shrishti", "PRIYANKA", "Mary   Kennedy", "Mary Kennady"])
def test_excluded_names(name):
    assert FDLLM.validate_client_name(name, SimpleNamespace()) is None


def test_distinct_client_with_shared_first_name():
    assert FDLLM.validate_client_name("Priyanka Shah", SimpleNamespace(extname="Priyanka")) == "Priyanka Shah"
    assert FDLLM.validate_client_name("Satish Kumar", SimpleNamespace(extname="Satish Kumar")) is None


def test_f3_persists_summary_in_existing_field(monkeypatch):
    from services.Database_sync import f3
    from storage.models import CallAnalysisResult
    db = MagicMock()
    record = SimpleNamespace(call_id=uuid4())
    db.query.return_value.filter.return_value.first.return_value = record
    analysis = CallAnalysisResult()
    processor = f3.F3Processor(application="flintdesk")
    monkeypatch.setattr(f3, "SessionLocal", lambda: db)
    monkeypatch.setattr(processor, "_get_or_create_analysis_record", lambda *args: analysis)
    summary = "Purpose and facts.\n\nQuestions, responses and concerns.\n\nCommitments, actions and next steps."
    processor._persist_flintdesk_result(record, {"fd_a_call_summary": summary})
    assert analysis.fd_a_call_summary == summary
    assert analysis.raw_llm_json["fd_a_call_summary"] == summary
    assert record.fd_processing_status == "processed"
    db.commit.assert_called_once()


def deletion_client(role, db):
    app = FastAPI()
    app.include_router(sync_filters.router)
    app.dependency_overrides[get_current_user] = lambda: {"role": role, "fd_user_id": str(uuid4())}
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_admin_delete_and_nonadmin_rejected():
    db = MagicMock()
    row = SimpleNamespace(id=uuid4(), filter_type="number", value="555", parent_number=None)
    db.query.return_value.filter.return_value.first.return_value = row
    assert deletion_client("admin", db).delete(f"/flintdesk/sync-filters/{row.id}").status_code == 204
    db.delete.assert_called_once_with(row)
    db.commit.assert_called_once()
    db.reset_mock()
    assert deletion_client("agent", db).delete(f"/flintdesk/sync-filters/{row.id}").status_code == 403
    db.delete.assert_not_called()


def test_delete_dependency_failure_rolls_back():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(filter_type="extension", value="101", parent_number="555")
    db.commit.side_effect = IntegrityError("delete", {}, Exception("dependency"))
    assert deletion_client("admin", db).delete(f"/flintdesk/sync-filters/{uuid4()}").status_code == 409
    db.rollback.assert_called_once()


def test_session_renewal_account_check_and_transient_failure(monkeypatch):
    app = FastAPI()
    app.include_router(fd_auth.router)
    client = TestClient(app)
    user_id = uuid4()
    token = create_access_token({"fd_user_id": str(user_id), "role": "agent"})
    headers = {"Authorization": f"Bearer {token}"}
    connection = MagicMock()
    connection.__enter__.return_value.execute.return_value.fetchone.return_value = (user_id, "Agent", "agent@example.test", True, "admin")
    monkeypatch.setattr(fd_auth.engine, "connect", lambda: connection)
    response = client.post("/fd-auth/renew", headers=headers)
    assert response.status_code == 200
    claims = verify_token(response.json()["access_token"])
    assert claims["role"] == "admin"
    assert claims["session_exp"] >= claims["exp"]
    connection.__enter__.return_value.execute.return_value.fetchone.return_value = None
    assert client.post("/fd-auth/renew", headers=headers).status_code == 401
    def unavailable():
        raise OperationalError("select", {}, Exception("unavailable"))
    monkeypatch.setattr(fd_auth.engine, "connect", unavailable)
    assert client.post("/fd-auth/renew", headers=headers).status_code == 503
    expired = create_access_token({"fd_user_id": str(user_id), "session_exp": 1})
    assert client.post("/fd-auth/renew", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
