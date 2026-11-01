import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import api, app, cors_allowed_origins


def test_cors_allows_production_origin_and_preflight():
    client = TestClient(app)
    origin = "https://autoflint.onrender.com"

    preflight = client.options(
        "/calls/",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()


def test_cors_headers_are_present_on_authentication_and_server_errors():
    origin = "https://autoflint.onrender.com"
    client = TestClient(app, raise_server_exceptions=False)

    unauthenticated = client.get("/auth/me", headers={"Origin": origin})
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["access-control-allow-origin"] == origin

    @api.get("/__test-unhandled-error")
    def raise_unhandled_error():
        raise RuntimeError("test-only failure")

    failed = client.get("/__test-unhandled-error", headers={"Origin": origin})
    assert failed.status_code == 500
    assert failed.json() == {"detail": "Internal server error"}
    assert failed.headers["access-control-allow-origin"] == origin


def test_cors_rejects_untrusted_remote_origins_and_keeps_localhost():
    client = TestClient(app)
    blocked = client.get("/auth/me", headers={"Origin": "https://attacker.example"})
    assert "access-control-allow-origin" not in blocked.headers

    local = client.get("/auth/me", headers={"Origin": "http://localhost:5173"})
    assert local.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_production_origin_is_present_without_render_environment_override(monkeypatch):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    assert cors_allowed_origins() == ["https://autoflint.onrender.com"]
