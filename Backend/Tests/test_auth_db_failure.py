import sys
from pathlib import Path

# Test setup: ensure the project root is on PYTHONPATH so the app imports correctly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from main import app


def test_login_returns_503_when_db_is_unavailable(monkeypatch):
    # Fix regression test: force a DB connection failure to simulate the outage path.
    def fake_connect(*args, **kwargs):
        raise OperationalError("statement", {}, Exception("database unavailable"))

    # Monkeypatch the engine's connect method to mimic a broken Postgres connection.
    monkeypatch.setattr("routes.auth.engine.connect", fake_connect)

    client = TestClient(app)
    response = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "wrongpass"},
    )

    # The endpoint should fail gracefully with a 503 and a clear client message.
    assert response.status_code == 503
    assert response.json()["detail"] == "Database unavailable"
