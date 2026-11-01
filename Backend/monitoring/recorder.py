"""Best-effort usage ledger writes called from the STT and LLM code paths.

Contract: nothing in this module may raise into, slow down materially, or change
the behaviour of call processing. Every failure is swallowed (logged without
payloads). Rows hold counts and identifiers only.
"""
import contextvars
import logging
import queue
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import text

logger = logging.getLogger("autoflint.monitoring")

_INSERT = text("""
    INSERT INTO provider_usage_events
      (event_key, provider, operation, status, call_id, model, audio_seconds,
       input_tokens, cached_input_tokens, output_tokens, http_status, error_class, occurred_at)
    VALUES
      (:event_key, :provider, :operation, :status, :call_id, :model, :audio_seconds,
       :input_tokens, :cached_input_tokens, :output_tokens, :http_status, :error_class, :occurred_at)
    ON CONFLICT (event_key) DO NOTHING
""")

_warned = False


def _engine():
    from storage.db import engine
    return engine


def record_event(provider, operation, status, *, call_id=None, model=None, audio_seconds=None,
                 input_tokens=None, cached_input_tokens=None, output_tokens=None,
                 http_status=None, error_class=None, event_key=None, engine=None,
                 occurred_at=None) -> bool:
    """Insert one ledger row. Returns True if written; never raises."""
    global _warned
    try:
        params = {
            "event_key": event_key or uuid.uuid4().hex, "provider": provider,
            "operation": operation, "status": status,
            "call_id": str(call_id) if call_id else None,
            "model": (model or None) and str(model)[:80],
            "audio_seconds": audio_seconds, "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens, "output_tokens": output_tokens,
            "http_status": http_status, "error_class": error_class,
            "occurred_at": occurred_at or datetime.now(timezone.utc),
        }
        with (engine or _engine()).begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SET LOCAL statement_timeout = '3s'"))
            conn.execute(_INSERT, params)
        return True
    except Exception as exc:  # noqa: BLE001 - monitoring must never break processing
        if not _warned:
            _warned = True
            logger.warning("usage ledger write skipped (%s); is migration 071 applied?", type(exc).__name__)
        return False


# --- non-blocking writer ----------------------------------------------------
# The hooks run on STT/LLM worker threads. A ledger write needs a pooled DB
# connection, so doing it inline could make a call-processing thread wait on the
# shared pool. Hooks therefore hand the row to one background daemon thread via a
# bounded queue; if the queue is full the row is dropped (monitoring loses a row,
# processing never waits). Rows still queued when the process exits are lost.
ASYNC_WRITES = True   # tests set False to write inline
_queue: "queue.Queue[dict]" = queue.Queue(maxsize=1000)
_worker = None
_worker_lock = threading.Lock()


def _drain():
    while True:
        item = _queue.get()
        try:
            record_event(*item.pop("args"), **item)
        except Exception:  # noqa: BLE001
            pass


def submit_event(*args, **kwargs) -> None:
    """Queue one ledger row for the background writer; never blocks, never raises."""
    global _worker
    try:
        kwargs.setdefault("occurred_at", datetime.now(timezone.utc))
        if not ASYNC_WRITES:
            record_event(*args, **kwargs)
            return
        with _worker_lock:
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(target=_drain, name="usage-ledger", daemon=True)
                _worker.start()
        _queue.put_nowait({"args": args, **kwargs})
    except Exception:  # noqa: BLE001 - includes queue.Full
        pass


# --- LLM -----------------------------------------------------------------

def usage_from_chat_response(body) -> dict:
    """Extract token usage from an OpenAI chat-completions response body.
    Absent fields stay None (never 0): a missing usage block is unknown."""
    usage = (body or {}).get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return {"input_tokens": None, "output_tokens": None, "cached_input_tokens": None}
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    return {
        "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
        "output_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
        "cached_input_tokens": details.get("cached_tokens") if isinstance(details, dict) else None,
    }


def record_llm_response(response) -> None:
    """Called once per successful (HTTP 2xx) chat request, i.e. per billable one."""
    try:
        body = response.json()
        submit_event("openai", "chat_completion", "success", model=body.get("model"),
                     http_status=response.status_code, **usage_from_chat_response(body))
    except Exception:  # noqa: BLE001
        pass


# --- STT -----------------------------------------------------------------

_stt_scope: contextvars.ContextVar = contextvars.ContextVar("stt_usage_scope", default=None)


def report_stt_audio_seconds(seconds) -> None:
    """Called by the STT client with the duration of the audio it is submitting."""
    try:
        scope = _stt_scope.get()
        if scope is not None and seconds is not None:
            scope["audio_seconds"] = seconds
    except Exception:  # noqa: BLE001
        pass


@contextmanager
def stt_usage_scope(call_id, model=None):
    """Wrap one STT attempt for one call. Records exactly one ledger row:
    'success' on normal exit, 'failed' if the body raises (which is re-raised)."""
    scope = {"audio_seconds": None}
    token = _stt_scope.set(scope)
    try:
        yield scope
    except BaseException as exc:
        submit_event("elevenlabs", "stt", "failed", call_id=call_id, model=model,
                     audio_seconds=scope["audio_seconds"], error_class=type(exc).__name__,
                     http_status=getattr(getattr(exc, "response", None), "status_code", None))
        raise
    else:
        submit_event("elevenlabs", "stt", "success", call_id=call_id, model=model,
                     audio_seconds=scope["audio_seconds"])
    finally:
        _stt_scope.reset(token)
