from datetime import date, timezone

from sqlalchemy import text

from monitoring import aggregation
from monitoring.audio import wav_duration_seconds
from monitoring.models import MEASURED, UNAVAILABLE, Window
from .conftest import add_call, add_event, add_job, utc

DAY = date(2026, 10, 8)
W = Window(DAY, "UTC")
IN = utc(2026, 10, 8, 12)
BEFORE = utc(2026, 10, 7, 23, 59, 59)
AFTER = utc(2026, 10, 9, 0, 0, 0)


def collect(engine):
    with engine.connect() as conn:
        return aggregation.collect_app_metrics(conn, W)


# ---- audio duration ---------------------------------------------------------
def _wav(byte_rate, data_len, declared=None):
    import struct
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, byte_rate, 2, 16)
    data = b"\0" * data_len
    return (b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", data_len if declared is None else declared) + data)


def test_wav_duration_pcm_and_streamed_header():
    assert wav_duration_seconds(_wav(16000, 160000)) == 10.0
    assert wav_duration_seconds(_wav(16000, 160000, declared=0xFFFFFFFF)) == 10.0  # streamed header
    assert wav_duration_seconds(b"not audio") is None
    assert wav_duration_seconds(_wav(0, 100)) is None


# ---- calls ingested / day boundaries -----------------------------------------
def test_calls_ingested_respects_window_edges(engine):
    with engine.begin() as c:
        add_call(c, "a", IN); add_call(c, "edge-start", utc(2026, 10, 8, 0, 0, 0))
        add_call(c, "before", BEFORE); add_call(c, "edge-end", AFTER)
    assert collect(engine)["calls_ingested"].value == 2


def test_window_uses_report_timezone_dst_aware():
    w = Window(date(2026, 10, 8), "America/Chicago")  # CDT = UTC-5
    assert w.start.hour == 5 and w.end.hour == 5 and not w.is_utc_aligned
    assert Window(date(2026, 11, 1), "America/Chicago").end - Window(date(2026, 11, 1), "America/Chicago").start \
        == __import__("datetime").timedelta(hours=25)  # fall-back day


# ---- STT --------------------------------------------------------------------
def test_stt_retries_and_reruns(engine):
    with engine.begin() as c:
        add_event(c, "1", IN, call_id="c1", status="failed", audio_seconds=60, error_class="Timeout")
        add_event(c, "2", IN, call_id="c1", audio_seconds=60)              # retry succeeded
        add_event(c, "3", IN, call_id="c2", audio_seconds=120)
        add_event(c, "4", IN, call_id="c2", audio_seconds=120)             # re-run: billed twice
        add_event(c, "5", IN, call_id="c3", status="failed", error_class="HTTPError")
        add_event(c, "6", IN, call_id="c3", status="failed", error_class="HTTPError")  # two failed tries, one call
        add_event(c, "7", BEFORE, call_id="c9", audio_seconds=600)         # other day
    m = collect(engine)
    assert m["calls_stt_completed"].value == 2          # distinct calls
    assert m["audio_minutes"].value == 5.0              # (60+120+120)/60, failed attempt excluded
    assert "1.00 min" in m["audio_minutes"].note        # wasted failed-attempt minutes disclosed
    assert m["stt_failures"].value == 1                 # c1 recovered, c3 did not


def test_stt_unknown_duration_is_flagged_not_zero_filled(engine):
    with engine.begin() as c:
        add_event(c, "1", IN, call_id="c1", audio_seconds=None)
        add_event(c, "2", IN, call_id="c2", audio_seconds=60)
    m = collect(engine)
    assert m["audio_minutes"].value == 1.0 and "lower bound" in m["audio_minutes"].note


def test_stt_without_any_ledger_is_unavailable_not_zero(engine):
    m = collect(engine)
    for key in ("calls_stt_completed", "audio_minutes", "stt_failures"):
        assert m[key].value is None and m[key].kind == UNAVAILABLE


def test_stt_zero_day_after_ledger_started_is_a_real_zero(engine):
    with engine.begin() as c:
        add_event(c, "old", utc(2026, 10, 1, 9), call_id="c1", audio_seconds=30)
    m = collect(engine)
    assert m["calls_stt_completed"].value == 0 and m["calls_stt_completed"].kind == MEASURED


# ---- LLM --------------------------------------------------------------------
USAGE = {"model": "gpt-5.4-mini-2026", "usage": {"prompt_tokens": 1000, "completion_tokens": 200,
                                                 "prompt_tokens_details": {"cached_tokens": 400}}}


def test_llm_counts_completed_jobs_only_and_failures_separately(engine):
    with engine.begin() as c:
        add_job(c, "j1", "c1", "completed", IN, USAGE)
        add_job(c, "j2", "c2", "completed", IN, USAGE)
        add_job(c, "j3", "c3", "review_required", IN)       # request outcome unknown
        add_job(c, "j4", "c4", "generating", IN)            # in flight: neither
        add_job(c, "j5", "c5", "completed", BEFORE, USAGE)  # other day
    m = collect(engine)
    assert m["llm_completed"].value == 2 and m["llm_failures"].value == 1


def test_llm_tokens_from_job_responses_when_no_ledger(engine):
    with engine.begin() as c:
        add_job(c, "j1", "c1", "completed", IN, USAGE)
        add_job(c, "j2", "c2", "response_ready", IN, USAGE)  # billed, not yet saved
        add_job(c, "j3", "c3", "completed", IN, {"choices": []})  # no usage block
    m = collect(engine)
    assert (m["llm_input_tokens"].value, m["llm_output_tokens"].value, m["llm_cached_input_tokens"].value) == (2000, 400, 800)
    assert "lacked usage" in m["llm_input_tokens"].note and "analysis_jobs" in m["llm_input_tokens"].source
    assert m["llm_models"].value == "gpt-5.4-mini-2026"


def test_llm_ledger_takes_precedence_and_is_not_added_to_job_fallback(engine):
    with engine.begin() as c:
        add_job(c, "j1", "c1", "completed", IN, USAGE)
        add_event(c, "e1", IN, provider="openai", operation="chat_completion", model="gpt-5.4-mini-2026",
                  input_tokens=1000, output_tokens=200, cached_input_tokens=400)
        add_event(c, "e2", IN, provider="openai", operation="chat_completion", model="gpt-5.4-mini-2026",
                  input_tokens=50, output_tokens=5)  # e.g. a stage-2 regeneration with no job
    m = collect(engine)
    assert m["llm_input_tokens"].value == 1050 and m["llm_output_tokens"].value == 205
    assert "provider_usage_events" in m["llm_input_tokens"].source


def test_llm_idle_day_has_zero_tokens_with_note(engine):
    m = collect(engine)
    assert m["llm_completed"].value == 0 and m["llm_input_tokens"].value == 0
