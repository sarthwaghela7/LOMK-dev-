"""Isolated fixtures: in-memory SQLite only. No network, no real DATABASE_URL,
no provider traffic. SQLite stands in for the three monitoring tables plus the
two app tables the aggregator reads (calls, analysis_jobs)."""
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from monitoring.config import Settings
from monitoring.schema import metadata


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE calls (call_id TEXT PRIMARY KEY, registered_at TIMESTAMP)"))
        conn.execute(text("CREATE TABLE analysis_jobs (id TEXT PRIMARY KEY, call_id TEXT, status TEXT, response TEXT, updated_at TIMESTAMP)"))
    return eng


@pytest.fixture
def settings():
    return Settings()


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def add_call(conn, call_id, when):
    conn.execute(text("INSERT INTO calls VALUES (:i, :w)"), {"i": call_id, "w": when})


def add_job(conn, job_id, call_id, status, when, response=None):
    conn.execute(text("INSERT INTO analysis_jobs VALUES (:i,:c,:s,:r,:w)"),
                 {"i": job_id, "c": call_id, "s": status, "r": json.dumps(response) if response else None, "w": when})


def add_event(conn, key, when, **kw):
    row = dict(provider="elevenlabs", operation="stt", status="success", call_id=None, model=None,
               audio_seconds=None, input_tokens=None, cached_input_tokens=None, output_tokens=None,
               http_status=None, error_class=None)
    row.update(kw)
    conn.execute(text("""INSERT INTO provider_usage_events
      (event_key, provider, operation, status, call_id, model, audio_seconds, input_tokens,
       cached_input_tokens, output_tokens, http_status, error_class, occurred_at)
      VALUES (:event_key,:provider,:operation,:status,:call_id,:model,:audio_seconds,:input_tokens,
       :cached_input_tokens,:output_tokens,:http_status,:error_class,:occurred_at)"""),
                 {**row, "event_key": key, "occurred_at": when})


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {}

    def json(self):
        return self._body


class FakeHttp:
    """Records calls; replies from a {url-substring: FakeResponse | Exception} map."""
    def __init__(self, replies=None):
        self.replies, self.calls = replies or {}, []

    def _reply(self, method, url, **kw):
        self.calls.append((method, url))
        for needle, reply in self.replies.items():
            if needle in url:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        return FakeResponse(404, {})

    def get(self, url, **kw):
        return self._reply("GET", url, **kw)

    def post(self, url, **kw):
        return self._reply("POST", url, **kw)


@pytest.fixture(autouse=True)
def inline_ledger_writes(monkeypatch):
    from monitoring import recorder
    monkeypatch.setattr(recorder, "ASYNC_WRITES", False)
