"""Database aggregation for one reporting day. Read-only.

Queries return small row sets bounded by the day's UTC window; all counting is
done in pure functions so retry/duplicate rules are unit-testable.

Definitions (also in MONITORING.md):
  calls_ingested     calls whose registered_at (call time; the table has no
                     insert timestamp) falls in the day.
  stt_completed      DISTINCT calls with >=1 successful STT ledger event that
                     day -- retries and re-runs of one call count once.
  audio_minutes      sum of audio_seconds of ALL successful STT events that day
                     (a re-run is billed again, so it is summed, not deduped).
  stt_failures       DISTINCT calls with a failed STT event that day and no
                     success for that call the same day.
  llm_completed      analysis_jobs with status='completed' updated that day
                     (UNIQUE per call/application/request_key => retries of a
                     job cannot double count; a re-analysis is a new job).
  llm_failures       analysis_jobs in 'review_required' updated that day.
  llm tokens         sum of usage on successful ledger requests; for days with
                     no ledger rows, falls back to the raw usage block stored in
                     analysis_jobs.response (labelled in provenance). The two
                     sources are never summed for the same day.
"""
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import text

from .models import MEASURED, UNAVAILABLE, Metric, Window, now_utc, unavailable
from .recorder import usage_from_chat_response


def _aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


# --------------------------------------------------------------------------
# Row fetchers (read-only SQL)
# --------------------------------------------------------------------------

def fetch_calls_registered(conn, window: Window):
    return conn.execute(text(
        "SELECT call_id FROM calls WHERE registered_at >= :s AND registered_at < :e"
    ), {"s": window.start, "e": window.end}).fetchall()


def fetch_ledger_events(conn, window: Window):
    return conn.execute(text("""
        SELECT provider, operation, status, call_id, model, audio_seconds,
               input_tokens, cached_input_tokens, output_tokens
        FROM provider_usage_events WHERE occurred_at >= :s AND occurred_at < :e
    """), {"s": window.start, "e": window.end}).mappings().all()


def fetch_analysis_jobs(conn, window: Window):
    return conn.execute(text("""
        SELECT id, call_id, status, response
        FROM analysis_jobs WHERE updated_at >= :s AND updated_at < :e
    """), {"s": window.start, "e": window.end}).mappings().all()


def fetch_ledger_exists_before(conn, window: Window) -> bool:
    return bool(conn.execute(text(
        "SELECT 1 FROM provider_usage_events WHERE occurred_at < :e LIMIT 1"
    ), {"e": window.end}).first())


# --------------------------------------------------------------------------
# Pure aggregation
# --------------------------------------------------------------------------

def aggregate_stt(events, window: Window, *, ledger_available: bool, at=None):
    at = at or now_utc()
    src = "provider_usage_events (STT ledger)"
    if not ledger_available:
        why = "STT usage ledger has no rows on or before this date (instrumentation not yet deployed); history cannot be reconstructed"
        return {k: unavailable(window, src, why, at) for k in
                ("calls_stt_completed", "audio_minutes", "stt_failures")}
    stt = [e for e in events if e["provider"] == "elevenlabs" and e["operation"] == "stt"]
    ok = [e for e in stt if e["status"] == "success"]
    bad = [e for e in stt if e["status"] == "failed"]
    ok_calls = {e["call_id"] for e in ok if e["call_id"]}
    failed_calls = {e["call_id"] for e in bad if e["call_id"]} - ok_calls
    unknown_dur = sum(1 for e in ok if e["audio_seconds"] is None)
    seconds = sum(float(e["audio_seconds"]) for e in ok if e["audio_seconds"] is not None)
    note = None
    if unknown_dur:
        note = f"{unknown_dur} successful STT request(s) had no measurable duration (non-WAV or unreadable header); minutes are a lower bound"
    wasted = sum(float(e["audio_seconds"]) for e in bad if e["audio_seconds"] is not None)
    if wasted:
        note = ((note + "; ") if note else "") + f"{wasted / 60:.2f} min were sent in failed attempts and are excluded"
    mk = lambda v, n=None: Metric(v, MEASURED, src, at, window.start, window.end, n)
    return {
        "calls_stt_completed": mk(len(ok_calls)),
        "audio_minutes": mk(round(seconds / 60, 4), note),
        "stt_failures": mk(len(failed_calls)),
    }


def aggregate_llm(events, jobs, window: Window, *, at=None):
    at = at or now_utc()
    completed = [j for j in jobs if j["status"] == "completed"]
    review = [j for j in jobs if j["status"] == "review_required"]
    src_jobs = "analysis_jobs"
    out = {
        "llm_completed": Metric(len(completed), MEASURED, src_jobs, at, window.start, window.end),
        "llm_failures": Metric(len(review), MEASURED, src_jobs + " (status=review_required)", at, window.start, window.end),
    }
    chat = [e for e in events if e["provider"] == "openai" and e["operation"] == "chat_completion"
            and e["status"] == "success"]
    if chat:
        src = "provider_usage_events (OpenAI response usage)"
        unknown = sum(1 for e in chat if e["input_tokens"] is None or e["output_tokens"] is None)
        note = f"{unknown} request(s) returned no usage block; tokens are a lower bound" if unknown else None
        inp = sum(e["input_tokens"] or 0 for e in chat)
        outp = sum(e["output_tokens"] or 0 for e in chat)
        cached = sum(e["cached_input_tokens"] or 0 for e in chat)
        models = sorted({e["model"] for e in chat if e["model"]})
    else:
        # Fallback: raw OpenAI response stored with each job (one per job, so
        # retries of the HTTP request that were rejected carry no usage).
        src = "analysis_jobs.response.usage (fallback; excludes non-job LLM calls)"
        inp = outp = cached = 0
        seen = unknown = 0
        models = set()
        for j in jobs:
            if j["status"] not in ("response_ready", "completed") or not j["response"]:
                continue
            body = j["response"]
            if isinstance(body, str):
                import json
                try:
                    body = json.loads(body)
                except ValueError:
                    continue
            u = usage_from_chat_response(body)
            seen += 1
            if u["input_tokens"] is None or u["output_tokens"] is None:
                unknown += 1
                continue
            inp += u["input_tokens"]; outp += u["output_tokens"]; cached += u["cached_input_tokens"] or 0
            if isinstance(body, dict) and body.get("model"):
                models.add(body["model"])
        models = sorted(models)
        if seen == 0 and not completed:
            note = "no LLM activity recorded"
        else:
            note = "from stored job responses" + (f"; {unknown} lacked usage" if unknown else "")
    base = lambda v: Metric(v, MEASURED, src, at, window.start, window.end, note)
    out["llm_input_tokens"] = base(inp)
    out["llm_output_tokens"] = base(outp)
    out["llm_cached_input_tokens"] = base(cached)
    out["llm_models"] = Metric(",".join(models) or None, MEASURED, src, at, window.start, window.end)
    return out


def collect_app_metrics(conn, window: Window, *, at=None) -> dict:
    """Entry point used by the report builder. ``conn`` must be read-only capable."""
    at = at or now_utc()
    calls = fetch_calls_registered(conn, window)
    events = fetch_ledger_events(conn, window)
    ledger_available = bool(events) or fetch_ledger_exists_before(conn, window)
    metrics = {"calls_ingested": Metric(len(calls), MEASURED, "calls.registered_at", at, window.start, window.end,
                                        "counted by call time (no insert timestamp exists); late-synced calls can raise this on re-run")}
    metrics.update(aggregate_stt(events, window, ledger_available=ledger_available, at=at))
    metrics.update(aggregate_llm(events, fetch_analysis_jobs(conn, window), window, at=at))
    return metrics
