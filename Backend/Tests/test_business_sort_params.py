from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.calls as calls_module
from middleware.auth import get_current_user
from storage.db import get_db


def _row(call_number, client_name, call_date, agent, status):
    return SimpleNamespace(
        call_number=call_number,
        client_name=client_name,
        call_recording_date=call_date,
        front_desk_person_name=agent,
        action_status=status,
        call_id=None,
    )


ROWS = [
    (_row(2, "Bravo", "2026-10-02", "Zed", "pending"), None),
    (_row(3, "alpha", "2026-10-03", "Amy", "Success - PC"), None),
    (_row(1, "Charlie", "2026-10-01", "Mia", "open"), None),
]


@pytest.fixture
def client(monkeypatch):
    # _fetch_filtered_case_rows returns rows newest-first.
    monkeypatch.setattr(
        calls_module,
        "_fetch_filtered_case_rows",
        lambda *a, **k: sorted(
            ROWS, key=lambda p: p[0].call_recording_date, reverse=True
        ),
    )
    app = FastAPI()
    app.include_router(calls_module.router)
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: {"role": "admin"}
    return TestClient(app)


def _numbers(resp):
    assert resp.status_code == 200
    return [r["call_number"] for r in resp.json()["results"]]


def test_default_sort_is_call_date_desc(client):
    assert _numbers(client.get("/calls/business")) == [3, 2, 1]


def test_explicit_default_matches_implicit(client):
    explicit = client.get("/calls/business?sort_by=call_date&sort_order=desc")
    assert _numbers(explicit) == [3, 2, 1]


@pytest.mark.parametrize("field", list(calls_module.BUSINESS_SORT_FIELDS))
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_every_sort_field_and_order_accepted(client, field, order):
    resp = client.get(f"/calls/business?sort_by={field}&sort_order={order}")
    assert resp.status_code == 200
    assert resp.json()["total"] == 3


def test_sort_by_client_name_asc_is_case_insensitive(client):
    resp = client.get("/calls/business?sort_by=client_name&sort_order=asc")
    assert _numbers(resp) == [3, 2, 1]


def test_sort_by_call_number_asc(client):
    resp = client.get("/calls/business?sort_by=call_number&sort_order=asc")
    assert _numbers(resp) == [1, 2, 3]


def test_unsupported_sort_by_falls_back_to_call_date_not_422(client):
    # Existing contract: enum was OpenAPI metadata only, never enforced.
    resp = client.get("/calls/business?sort_by=bogus&sort_order=desc")
    assert _numbers(resp) == [3, 2, 1]


def test_unsupported_sort_order_sorts_ascending_not_422(client):
    resp = client.get("/calls/business?sort_by=call_date&sort_order=sideways")
    assert _numbers(resp) == [1, 2, 3]


def test_openapi_still_advertises_enums(client):
    params = client.app.openapi()["paths"]["/calls/business"]["get"]["parameters"]
    by_name = {p["name"]: p["schema"] for p in params}
    assert by_name["sort_by"]["default"] == "call_date"
    assert by_name["sort_by"]["enum"] == list(calls_module.BUSINESS_SORT_FIELDS)
    assert by_name["sort_order"]["default"] == "desc"
    assert by_name["sort_order"]["enum"] == ["asc", "desc"]
