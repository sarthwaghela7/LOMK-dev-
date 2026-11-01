"""The three production hook points, driven with mocked HTTP/S3 (no traffic)."""
import os
import struct

os.environ.setdefault("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")  # never connected; engine is created lazily
os.environ.setdefault("LLM_API_KEY", "test"); os.environ.setdefault("STT_API_KEY", "test")

import pytest
from sqlalchemy import text

from monitoring import recorder
from .conftest import FakeResponse


def wav(seconds, byte_rate=16000):
    n = int(seconds * byte_rate)
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, byte_rate, 2, 16)
    return b"RIFF" + struct.pack("<I", 36 + n) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", n) + b"\0" * n


@pytest.fixture
def ledger(engine, monkeypatch):
    monkeypatch.setattr(recorder, "_engine", lambda: engine)
    return engine


def rows(engine):
    with engine.connect() as c:
        return c.execute(text("SELECT provider,operation,status,call_id,audio_seconds,input_tokens,output_tokens,model "
                              "FROM provider_usage_events ORDER BY id")).all()


def make_stt(monkeypatch, tmp_path, post, audio):
    from services.Processing_pipeline import stt_node

    class FakeS3:
        def download_file(self, key, path):
            open(path, "wb").write(audio)

    monkeypatch.setattr(stt_node, "S3Service", FakeS3)
    monkeypatch.setattr(stt_node, "get_front_desk_keyterms", lambda: [])
    monkeypatch.setattr(stt_node.requests, "post", post)
    monkeypatch.setattr(stt_node, "STT_API_KEY", "k")
    return stt_node.STT()


class OkResp:
    ok, status_code, headers = True, 200, {}

    def json(self):
        return {"text": "hello", "words": []}


def test_stt_success_records_submitted_audio_seconds(ledger, monkeypatch, tmp_path):
    stt = make_stt(monkeypatch, tmp_path, lambda *a, **k: OkResp(), wav(12.5))
    with recorder.stt_usage_scope("call-9"):
        assert stt.transcribe("s3://b/k.wav") == "hello"
    assert rows(ledger) == [("elevenlabs", "stt", "success", "call-9", 12.5, None, None, None)]


def test_stt_provider_error_records_failure_with_audio_and_still_raises(ledger, monkeypatch, tmp_path):
    import requests

    class Bad:
        ok, status_code, headers, text = False, 500, {}, "server error"

    stt = make_stt(monkeypatch, tmp_path, lambda *a, **k: Bad(), wav(3))
    with pytest.raises(requests.exceptions.HTTPError):
        with recorder.stt_usage_scope("call-8"):
            stt.transcribe("s3://b/k.wav")
    assert rows(ledger) == [("elevenlabs", "stt", "failed", "call-8", 3.0, None, None, None)]


def test_stt_without_scope_writes_nothing(ledger, monkeypatch, tmp_path):
    stt = make_stt(monkeypatch, tmp_path, lambda *a, **k: OkResp(), wav(1))
    stt.transcribe("s3://b/k.wav")   # e.g. /ai/transcribe path: behaviour unchanged, no ledger row
    assert rows(ledger) == []


def test_stt_ledger_failure_does_not_change_result(monkeypatch, tmp_path):
    monkeypatch.setattr(recorder, "_engine", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    stt = make_stt(monkeypatch, tmp_path, lambda *a, **k: OkResp(), wav(1))
    with recorder.stt_usage_scope("c"):
        assert stt.transcribe("s3://b/k.wav") == "hello"


def test_llm_post_records_usage_once_per_billable_response_not_for_429(ledger, monkeypatch):
    from services.Processing_pipeline import fd_llm_node as node

    class Limited(FakeResponse):
        headers = {}
        text = ""

        def raise_for_status(self):
            pass

    class Ok(Limited):
        pass

    replies = [Limited(429, {"error": {"code": "rate_limit_exceeded"}}),
               Ok(200, {"model": "gpt-5.4-mini-x", "usage": {"prompt_tokens": 900, "completion_tokens": 80}})]
    monkeypatch.setattr(node.requests, "post", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr(node.time, "sleep", lambda s: None)
    monkeypatch.setattr(node, "_RATE_LIMITER", type("R", (), {"acquire": lambda s: None, "throttle_down": lambda s: 1,
                                                              "recover_up": lambda s: None})())
    llm = node.FDLLM.__new__(node.FDLLM)
    llm.url, llm.api_key, llm.model = "http://x", "k", "m"
    llm._post_with_retry({"model": "m"})
    assert rows(ledger) == [("openai", "chat_completion", "success", None, None, 900, 80, "gpt-5.4-mini-x")]
