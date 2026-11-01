"""Priority Cases against a real (disposable, local) PostgreSQL schema: claim/idempotency, retries, failure isolation,
the Calls-page-scoped read with duplicate merging, date boundaries and the HTTP access rules.

    PRIORITY_CASES_TEST_DATABASE_URL=postgresql+psycopg2://user@127.0.0.1:54331/pctest pytest Tests/test_priority_cases_db.py

Skipped unless that variable points at a local database. The model is a deterministic stub; nothing goes over the network.
"""
import json
import os
import uuid
from datetime import date, time, timedelta
from importlib import import_module

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from services import priority_cases as pc
from services import priority_cases_read as reader
from Tests.test_priority_cases import PROMPT, StubLLM, TRANSCRIPT, answer


@pytest.fixture
def env(monkeypatch):
    url = os.getenv("PRIORITY_CASES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set PRIORITY_CASES_TEST_DATABASE_URL to a disposable local PostgreSQL database")
    assert make_url(url).host in {"localhost", "127.0.0.1"}
    from storage.models import Base
    schema = "test_pc_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema}"})
    for table in Base.metadata.sorted_tables:        # a few unrelated models carry a literal uuid default PostgreSQL rejects; skip those
        try:
            with engine.begin() as conn:
                table.create(conn)
        except Exception:  # noqa: BLE001
            pass
    for name, function in (("migrations.069_create_fd_prompts", "migrate_create_fd_prompts"),
                           ("migrations.074_priority_cases", "migrate_priority_cases")):
        module = import_module(name)
        monkeypatch.setattr(module, "engine", engine)
        getattr(module, function)()
    factory = sessionmaker(bind=engine)
    try:
        yield SimpleEnv(engine, factory)
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


class SimpleEnv:
    def __init__(self, engine, factory):
        self.engine, self.factory = engine, factory
        self._n = 0

    def call(self, day, transcript, *, clock=None, src="8475550101", dst="2243374447", billsec=60, analysis=None, status="not processed"):
        self._n += 1
        call_id = str(uuid.uuid4())
        clock = clock or time(9, self._n % 60, 0)
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO calls (call_id, src, dst, billsec, call_date, call_time, transcript, fd_processing_status, call_number)
                                 VALUES (CAST(:id AS uuid), :src, :dst, :bs, :d, :t, :tr, :st, :n)"""),
                         {"id": call_id, "src": src, "dst": dst, "bs": billsec, "d": day, "t": clock, "tr": transcript, "st": status, "n": self._n})
            if analysis is not None:
                conn.execute(text("""INSERT INTO call_analysis_results (call_id, fd_a_client_name, fd_a_email_address, fd_a_case_type, fd_a_call_summary)
                                     VALUES (:id, :n, :e, :c, :s)"""),
                             {"id": call_id, "n": analysis.get("name"), "e": analysis.get("email"), "c": analysis.get("case_type"), "s": analysis.get("summary")})
        return call_id

    def row(self, call_id):
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT * FROM call_priority_analysis WHERE call_id = CAST(:i AS uuid)"), {"i": call_id}).mappings().first()

    def run(self, llm, start=date(2026, 10, 1), end=date(2026, 10, 31), limit=50, **kw):
        kw.setdefault("reanalyse", True)             # these tests exercise the backfill; the worker mode is tested explicitly
        return pc.run_batch(self.factory, lambda: llm, start=start, end=end, limit=limit, prompt=PROMPT, **kw)


OCT = date(2026, 10, 5)
RFE_ANSWER = answer(**{"RFE": {"status": "confirmed", "evidence": "I received an RFE for my I-485", "reason": "r", "confidence": "high"},
                       "I-485": {"status": "confirmed", "evidence": "RFE for my I-485 last week", "reason": "r", "confidence": "high"}})


def test_backfill_sends_every_usable_transcript_to_the_model_and_is_idempotent(env):
    mention = env.call(OCT, TRANSCRIPT)
    quiet = env.call(OCT, "Client asked about office hours and parking.")
    no_transcript = env.call(OCT, None)
    blank = env.call(OCT, "   ")
    outside = env.call(date(2026, 9, 30), TRANSCRIPT)
    llm = StubLLM(RFE_ANSWER, answer())

    counts = env.run(llm)
    assert (counts["eligible"], counts["analysed"], counts["matched"]) == (2, 2, 1)
    assert (counts["keyword_hint"], counts["no_keyword_hint"]) == (1, 1)    # diagnostics only
    assert len(llm.requests) == 2                                    # one structured request per call covers all five forms
    assert counts["input_tokens"] == 1400 and counts["output_tokens"] == 240
    row = env.row(mention)
    assert row["analysis_status"] == "completed" and row["analysis_source"] == "llm"
    assert row["confirmed_forms"] == ["RFE", "I-485"] and row["prompt_version"] == 3 and row["model"] == "stub-model"
    assert row["input_tokens"] == 700 and row["attempts"] == 1 and row["transcript_hash"] == pc.transcript_hash(TRANSCRIPT)
    quiet_row = env.row(quiet)           # no keyword, yet analysed by the model and recorded as such
    assert quiet_row["analysis_source"] == "llm" and quiet_row["confirmed_forms"] == [] and quiet_row["input_tokens"] == 700
    assert env.row(no_transcript) is None and env.row(blank) is None and env.row(outside) is None   # never transcribed, never out of range

    again = env.run(StubLLM())                                       # a second run must not ask the model anything
    assert again["eligible"] == 0 and again["llm_requests"] == 0
    with env.engine.connect() as conn:                               # no duplicate rows from repeated runs
        assert conn.execute(text("SELECT count(*), count(DISTINCT call_id) FROM call_priority_analysis")).first() == (2, 2)


CONTEXT_ONLY = "Client: USCIS sent me a letter asking for more documents about my case, the deadline is in thirty days."


def test_a_transcript_naming_no_form_is_classified_by_the_model_not_by_keywords(env):
    call = env.call(OCT, CONTEXT_ONLY)
    assert pc.candidate_forms(CONTEXT_ONLY) == set()
    llm = StubLLM(answer(RFE={"status": "confirmed", "reason": "USCIS asked for more documents",
                              "evidence": "USCIS sent me a letter asking for more documents about my case", "confidence": "medium"}))
    counts = env.run(llm)
    assert counts["analysed"] == 1 and len(llm.requests) == 1 and counts["no_keyword_hint"] == 1
    row = env.row(call)
    assert row["analysis_source"] == "llm" and row["confirmed_forms"] == ["RFE"]
    assert CONTEXT_ONLY in llm.requests[0]["messages"][1]["content"]


def test_keyword_hints_never_override_the_validated_model_result(env):
    text_with_keywords = "Just asking in general what an RFE or an I-485 is. Nothing is filed for me."
    call = env.call(OCT, text_with_keywords, analysis={"case_type": "I-485 / RFE", "summary": "I-485 RFE"})
    counts = env.run(StubLLM(answer()))
    assert counts["analysed"] == 1 and counts["keyword_hint"] == 1
    assert env.row(call)["confirmed_forms"] == [] and env.row(call)["analysis_source"] == "llm"


def test_an_ambiguous_transcript_stays_uncertain(env):
    ambiguous = "Client: about the petition, the one for her, I am not sure what happened to it."
    call = env.call(OCT, ambiguous)
    env.run(StubLLM(answer(**{"I-130": {"status": "uncertain", "reason": "unclear petition", "evidence": "about the petition, the one for her", "confidence": "low"}})))
    row = env.row(call)
    assert row["uncertain_forms"] == ["I-130"] and row["confirmed_forms"] == []


def _seed_completed(env, call, transcript, *, source, rev, version=3):
    result = json.dumps({**pc.empty_result(), "rev": rev, "prompt_version": version})
    with env.engine.begin() as conn:
        conn.execute(text("""INSERT INTO call_priority_analysis
                (call_id, analysis_status, analysis_source, transcript_hash, prompt_version, result, attempts, analyzed_at)
                VALUES (CAST(:i AS uuid), 'completed', :s, :h, :v, CAST(:r AS JSONB), 1, now())"""),
                     {"i": call, "s": source, "h": pc.transcript_hash(transcript), "v": version, "r": result})


def test_rows_stored_by_the_old_keyword_gate_are_reanalysed_but_model_answers_are_not(env):
    old_gate = env.call(OCT, CONTEXT_ONLY)                       # rev 1 'prefilter': the model never saw it
    paid = env.call(OCT, TRANSCRIPT)                             # rev 1 'llm': already paid for
    routing_leg = env.call(OCT, TRANSCRIPT + " routing leg")
    _seed_completed(env, old_gate, CONTEXT_ONLY, source="prefilter", rev="1")
    _seed_completed(env, paid, TRANSCRIPT, source="llm", rev="1")
    _seed_completed(env, routing_leg, TRANSCRIPT + " routing leg", source="prefilter", rev="2")   # not-listed rows are current
    with env.factory() as db:
        assert pc.claim(db, paid, TRANSCRIPT, 3) is None and pc.claim(db, routing_leg, TRANSCRIPT + " routing leg", 3) is None
        state = pc.coverage(db, start=date(2026, 10, 1), end=date(2026, 10, 31), version=3)
    assert state["with_transcript"] == 3 and state["up_to_date"] == 2     # the old-gate row is the only one out of date

    llm = StubLLM(answer(RFE={"status": "confirmed", "reason": "r", "confidence": "low",
                              "evidence": "USCIS sent me a letter asking for more documents about my case"}))
    counts = env.run(llm)
    assert counts["eligible"] == 1 and counts["analysed"] == 1 and len(llm.requests) == 1
    assert env.row(old_gate)["analysis_source"] == "llm" and env.row(old_gate)["confirmed_forms"] == ["RFE"]
    assert env.row(old_gate)["result"]["rev"] == pc.RESULT_REV
    assert env.run(StubLLM())["eligible"] == 0                   # and now everything is current


def test_a_new_prompt_version_also_reanalyses_prefiltered_rows(env):
    call = env.call(OCT, CONTEXT_ONLY)
    _seed_completed(env, call, CONTEXT_ONLY, source="prefilter", rev="1", version=1)
    llm = StubLLM(answer())
    counts = pc.run_batch(env.factory, lambda: llm, start=date(2026, 10, 1), end=date(2026, 10, 31), limit=50, prompt=dict(PROMPT, version=2),
                          reanalyse=True)
    assert counts["analysed"] == 1 and env.row(call)["prompt_version"] == 2


def test_a_changed_transcript_or_new_prompt_version_is_reanalysed_but_nothing_else(env):
    a = env.call(OCT, TRANSCRIPT)
    b = env.call(OCT, TRANSCRIPT + " Also my H-4 EAD.")
    env.run(StubLLM(answer(), answer()))
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE calls SET transcript = transcript || ' More about the RFE.' WHERE call_id = CAST(:i AS uuid)"), {"i": a})
    llm = StubLLM(answer())
    counts = env.run(llm)
    assert counts["eligible"] == 1 and len(llm.requests) == 1
    assert env.row(a)["transcript_hash"] != env.row(b)["transcript_hash"]

    newer = dict(PROMPT, version=4)
    llm = StubLLM(answer(), answer())
    counts = pc.run_batch(env.factory, lambda: llm, start=date(2026, 10, 1), end=date(2026, 10, 31), limit=50, prompt=newer,
                          reanalyse=True)
    assert counts["eligible"] == 2 and counts["analysed"] == 2 and env.row(a)["prompt_version"] == 4


def test_the_worker_mode_never_reanalyses_existing_results_but_still_handles_new_calls(env):
    old_gate = env.call(OCT, CONTEXT_ONLY)                       # rev 1 'prefilter' row from the old keyword gate
    paid = env.call(OCT, TRANSCRIPT)                             # rev 1 'llm' row under an older prompt version
    _seed_completed(env, old_gate, CONTEXT_ONLY, source="prefilter", rev="1", version=1)
    _seed_completed(env, paid, TRANSCRIPT, source="llm", rev="1", version=1)
    fresh = env.call(OCT, "Client asked about a work permit for her H-4 spouse status.")
    llm = StubLLM(answer())
    newer = dict(PROMPT, version=2)                              # a newer active prompt version, as after migration 075
    counts = pc.run_batch(env.factory, lambda: llm, start=date(2026, 10, 1), end=date(2026, 10, 31), limit=50, prompt=newer)
    assert counts["eligible"] == 1 and counts["analysed"] == 1 and len(llm.requests) == 1   # only the call with no result
    assert env.row(fresh)["prompt_version"] == 2 and env.row(fresh)["analysis_source"] == "llm"
    assert env.row(old_gate)["analysis_source"] == "prefilter" and env.row(old_gate)["prompt_version"] == 1
    assert env.row(paid)["prompt_version"] == 1 and env.row(paid)["analysis_source"] == "llm"
    again = pc.run_batch(env.factory, lambda: StubLLM(), start=date(2026, 10, 1), end=date(2026, 10, 31), limit=50, prompt=newer)
    assert again["eligible"] == 0
    # an explicit backfill is the only thing that re-analyses them
    backfill = pc.run_batch(env.factory, lambda: StubLLM(answer(), answer()), start=date(2026, 10, 1), end=date(2026, 10, 31),
                            limit=50, prompt=newer, reanalyse=True)
    assert backfill["analysed"] == 2


def test_the_worker_mode_still_retries_failed_and_changed_transcripts(env):
    call = env.call(OCT, TRANSCRIPT)
    pc.run_batch(env.factory, lambda: StubLLM(RuntimeError("x")), start=date(2026, 10, 1), end=date(2026, 10, 31), limit=50, prompt=PROMPT)
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE call_priority_analysis SET last_attempt_at = now() - interval '2 hours'"))
    done = pc.run_batch(env.factory, lambda: StubLLM(answer()), start=date(2026, 10, 1), end=date(2026, 10, 31), limit=50, prompt=PROMPT)
    assert done["analysed"] == 1
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE calls SET transcript = transcript || ' New words.' WHERE call_id = CAST(:i AS uuid)"), {"i": call})
    changed = pc.run_batch(env.factory, lambda: StubLLM(answer()), start=date(2026, 10, 1), end=date(2026, 10, 31), limit=50, prompt=PROMPT)
    assert changed["analysed"] == 1                              # a changed transcript is new input


def _prompt_rows(env):
    with env.engine.connect() as conn:
        return conn.execute(text("SELECT version, is_active, updated_by, system_prompt FROM fd_prompts "
                                 "WHERE prompt_key = 'priority_case_classification' ORDER BY version")).fetchall()


def test_migration_075_activates_the_contextual_prompt_once_and_keeps_admin_edits(env, monkeypatch):
    from migrations import __name__ as _  # noqa: F401  (package import check)
    module = import_module("migrations.075_priority_prompt_contextual")
    monkeypatch.setattr(module, "engine", env.engine)
    module.migrate_priority_prompt_contextual()
    rows = _prompt_rows(env)
    assert [(r[0], r[1]) for r in rows] == [(1, False), (2, True)] and rows[1][2] == "migration:075"
    assert rows[1][3].strip() == module.SYSTEM_PROMPT.strip()
    module.migrate_priority_prompt_contextual()                  # rerun: nothing new
    assert len(_prompt_rows(env)) == 2


def test_migration_075_does_not_overwrite_an_administrators_edit(env, monkeypatch):
    module = import_module("migrations.075_priority_prompt_contextual")
    monkeypatch.setattr(module, "engine", env.engine)
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE fd_prompts SET updated_by = 'admin@example.com' WHERE prompt_key = 'priority_case_classification'"))
    module.migrate_priority_prompt_contextual()
    rows = _prompt_rows(env)
    assert [(r[0], r[1]) for r in rows] == [(1, True)]


def test_claim_is_exclusive_and_completed_work_cannot_be_claimed(env):
    call = env.call(OCT, TRANSCRIPT)
    with env.factory() as db:
        assert pc.claim(db, call, TRANSCRIPT, 3) == 1
        assert pc.claim(db, call, TRANSCRIPT, 3) is None             # another worker holds a fresh lease
        assert pc.store(db, call, TRANSCRIPT, {**pc.empty_result()}, source="llm", model="m", usage=None)
        assert pc.claim(db, call, TRANSCRIPT, 3) is None             # already completed for this transcript + version
        assert pc.claim(db, call, TRANSCRIPT + "!", 3) == 1          # changed transcript: attempts restart


def test_an_expired_lease_is_picked_up_again(env):
    call = env.call(OCT, TRANSCRIPT)
    with env.factory() as db:
        pc.claim(db, call, TRANSCRIPT, 3)
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE call_priority_analysis SET claimed_at = now() - interval '1 hour'"))
    counts = env.run(StubLLM(answer()))
    assert counts["analysed"] == 1 and env.row(call)["analysis_status"] == "completed"


def test_failures_are_recorded_retried_after_the_delay_and_stop_after_max_attempts(env):
    call = env.call(OCT, TRANSCRIPT)
    first = env.run(StubLLM(RuntimeError("socket closed with secret client text")))
    assert first["failed"] == 1
    row = env.row(call)
    assert row["analysis_status"] == "failed" and row["attempts"] == 1 and row["error"] == "RuntimeError"   # no transcript text in the error
    assert env.run(StubLLM())["eligible"] == 0                      # not retried immediately

    for attempt in (2, 3):
        with env.engine.begin() as conn:
            conn.execute(text("UPDATE call_priority_analysis SET last_attempt_at = now() - interval '2 hours'"))
        counts = env.run(StubLLM("garbage", "garbage"))              # permanent validation failure
        assert counts["failed"] == 1 and env.row(call)["attempts"] == attempt
    assert "invalid model output" in env.row(call)["error"]
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE call_priority_analysis SET last_attempt_at = now() - interval '2 hours'"))
    assert env.run(StubLLM())["eligible"] == 0                      # attempts exhausted: no endless retries

    with env.engine.begin() as conn:                                 # a changed transcript starts over
        conn.execute(text("UPDATE calls SET transcript = transcript || ' RFE again' WHERE call_id = CAST(:i AS uuid)"), {"i": call})
    assert env.run(StubLLM(answer()))["analysed"] == 1


def test_provider_slowdown_releases_the_claim_without_using_an_attempt(env):
    from services.Processing_pipeline.fd_llm_node import LLMRateLimitExhausted
    a, b = env.call(OCT, TRANSCRIPT), env.call(OCT, TRANSCRIPT + " again")
    counts = env.run(StubLLM(LLMRateLimitExhausted("429")))
    assert counts["provider_busy"] and counts["analysed"] == 0
    row = env.row(a)
    assert row["analysis_status"] == "pending" and row["attempts"] == 0 and env.row(b) is None   # stopped at the first slow-down
    again = env.run(StubLLM(answer(), answer()))
    assert again["analysed"] == 2


def test_priority_failure_never_touches_the_calls_page_analysis(env):
    call = env.call(OCT, TRANSCRIPT, analysis={"name": "Asha", "summary": "S"}, status="processed")
    with env.engine.connect() as conn:
        before = (conn.execute(text("SELECT * FROM call_analysis_results")).mappings().all(),
                  conn.execute(text("SELECT fd_processing_status, error_message, updated_at FROM calls")).mappings().all())
    env.run(StubLLM(RuntimeError("boom")))
    env.run(StubLLM(answer()))
    with env.engine.connect() as conn:
        after = (conn.execute(text("SELECT * FROM call_analysis_results")).mappings().all(),
                 conn.execute(text("SELECT fd_processing_status, error_message, updated_at FROM calls")).mappings().all())
    assert after == before
    assert env.row(call) is not None


def test_should_stop_lets_the_worker_yield_to_f3(env):
    env.call(OCT, TRANSCRIPT)
    counts = env.run(StubLLM(), should_stop=lambda: True)
    assert counts["stopped"] and counts["analysed"] == 0


def test_calls_outside_the_calls_page_are_not_sent_to_the_model(env):
    listed, hidden = env.call(OCT, TRANSCRIPT), env.call(OCT, TRANSCRIPT + " routing leg")
    llm = StubLLM(RFE_ANSWER)
    counts = env.run(llm, is_listed=lambda day: {listed})
    assert counts["analysed"] == 1 and counts["not_listed"] == 1 and len(llm.requests) == 1
    assert env.row(hidden)["analysis_source"] == "prefilter"


def test_coverage_counts_missing_transcripts_separately(env):
    env.call(OCT, TRANSCRIPT)
    env.call(OCT, None)
    env.run(StubLLM(RFE_ANSWER))
    with env.factory() as db:
        state = pc.coverage(db, start=date(2026, 10, 1), end=date(2026, 10, 31), version=3)
    assert state["missing_transcript"] == 1 and state["with_transcript"] == 1 and state["up_to_date"] == 1
    assert state["with_confirmed"] == 1 and state["llm_analyses"] == 1 and state["input_tokens"] == 700


# ---------------------------------------------------------------- the read side
def confirmed(**forms):
    base = {f: {"status": "not_confirmed", "evidence": None, "reason": None, "confidence": None} for f in pc.FORMS}
    for form, evidence in forms.items():
        base[form.replace("_", "-") if form in {"I_130", "I_485"} else form] = {"status": "confirmed", "evidence": evidence, "reason": "r", "confidence": "high"}
    return {"forms": base}


def seed_result(env, call_id, transcript, result, status="completed"):
    with env.factory() as db:
        pc.claim(db, call_id, transcript, 3)
        pc.store(db, call_id, transcript, {**result, "prompt_version": 3}, source="llm", model="m", usage=None)
        if status != "completed":
            db.execute(text("UPDATE call_priority_analysis SET analysis_status = :s WHERE call_id = CAST(:i AS uuid)"), {"s": status, "i": call_id})
            db.commit()


def read(env, start=date(2026, 10, 1), end=date(2026, 10, 31), forms=pc.FORMS, status="confirmed"):
    with env.factory() as db:
        return reader.priority_cases(db, start=start, end=end, form_types=forms, status=status)


def test_read_returns_each_confirmed_call_once_per_form_with_calls_page_details(env):
    with env.engine.begin() as conn:
        conn.execute(text("INSERT INTO fd_sync_filters (id, filter_type, value) VALUES (gen_random_uuid(), 'number', '2243374447')"))
    call = env.call(OCT, TRANSCRIPT, analysis={"name": "Asha Rao", "email": "asha@example.com", "case_type": "I-485", "summary": "Pending I-485 with RFE"})
    seed_result(env, call, TRANSCRIPT, confirmed(RFE="I received an RFE", **{"I_485": "RFE for my I-485"}))
    other = env.call(OCT, "H4 only call H-4 EAD", analysis={"name": "B"})
    seed_result(env, other, "H4 only call H-4 EAD", confirmed(H4EAD="H-4 EAD"))

    found = read(env)
    assert [r["call_id"] for r in found["RFE"]["rows"]] == [call] and found["RFE"]["total"] == 1
    assert [r["call_id"] for r in found["I-485"]["rows"]] == [call]          # same call in two tables, once in each
    assert found["I-130"]["rows"] == [] and found["H4"]["rows"] == []
    row = found["RFE"]["rows"][0]
    assert (row["name"], row["email"], row["case_type"], row["call_summary"]) == ("Asha Rao", "asha@example.com", "I-485", "Pending I-485 with RFE")
    assert row["date"] == "2026-10-05" and row["phone"] == "8475550101" and row["found_in"] == 'Transcript: "I received an RFE"'
    assert row["transcript"] == TRANSCRIPT and row["duplicates_merged"] == 0
    assert [r["call_id"] for r in found["H4EAD"]["rows"]] == [other]


def test_read_respects_inclusive_date_boundaries(env):
    last, first, before, after = (env.call(d, TRANSCRIPT) for d in (date(2026, 10, 31), date(2026, 10, 1), date(2026, 9, 30), date(2026, 11, 1)))
    for call in (last, first, before, after):
        seed_result(env, call, TRANSCRIPT, confirmed(RFE="I received an RFE"))
    ids = {r["call_id"] for r in read(env, forms=("RFE",))["RFE"]["rows"]}
    assert ids == {last, first}
    assert {r["call_id"] for r in read(env, date(2026, 10, 31), date(2026, 10, 31), ("RFE",))["RFE"]["rows"]} == {last}


def test_only_confirmed_results_are_shown_not_pending_failed_or_uncertain(env):
    ok, pending, failed, unsure, nothing = (env.call(OCT, TRANSCRIPT, clock=time(10, i)) for i in range(5))
    seed_result(env, ok, TRANSCRIPT, confirmed(RFE="I received an RFE"))
    seed_result(env, pending, TRANSCRIPT, confirmed(RFE="I received an RFE"), status="pending")
    seed_result(env, failed, TRANSCRIPT, confirmed(RFE="I received an RFE"), status="failed")
    uncertain = confirmed()
    uncertain["forms"]["RFE"] = {"status": "uncertain", "evidence": "I received an RFE", "reason": "unclear", "confidence": "low"}
    seed_result(env, unsure, TRANSCRIPT, uncertain)
    seed_result(env, nothing, TRANSCRIPT, confirmed())
    assert [r["call_id"] for r in read(env, forms=("RFE",))["RFE"]["rows"]] == [ok]
    assert [r["call_id"] for r in read(env, forms=("RFE",), status="uncertain")["RFE"]["rows"]] == [unsure]
    with env.factory() as db:
        coverage = reader.coverage_summary(db, start=date(2026, 10, 1), end=date(2026, 10, 31))
    assert coverage["calls"] == 5 and coverage["failed"] == 1 and coverage["pending"] == 1 and coverage["uncertain"] == 1


def test_duplicate_legs_of_one_call_are_merged_into_one_row(env):
    clock = time(11, 30, 0)
    canonical = env.call(OCT, TRANSCRIPT, clock=clock, billsec=300)
    leg = env.call(OCT, TRANSCRIPT, clock=clock, billsec=45)
    seed_result(env, leg, TRANSCRIPT, confirmed(RFE="I received an RFE"))     # the evidence sits on the shorter duplicate leg
    rows = read(env, forms=("RFE",))["RFE"]["rows"]
    assert len(rows) == 1 and rows[0]["call_id"] == canonical and rows[0]["duplicates_merged"] == 1
    seed_result(env, canonical, TRANSCRIPT, confirmed(RFE="I received an RFE"))
    rows = read(env, forms=("RFE",))["RFE"]["rows"]
    assert len(rows) == 1                                                      # confirmed on both legs: still once


def test_routing_and_missed_call_records_are_not_listed(env):
    with env.engine.begin() as conn:
        conn.execute(text("INSERT INTO fd_sync_filters (id, filter_type, value, target_page) VALUES (gen_random_uuid(), 'number', '2243860601', 'missed_calls')"))
    missed = env.call(OCT, TRANSCRIPT, dst="2243860601")
    seed_result(env, missed, TRANSCRIPT, confirmed(RFE="I received an RFE"))
    assert read(env, forms=("RFE",))["RFE"]["rows"] == []


def test_each_form_is_limited_and_reports_truncation(env, monkeypatch):
    monkeypatch.setattr(reader, "ROW_LIMIT_PER_FORM", 2)
    for i in range(3):
        call = env.call(OCT, TRANSCRIPT, clock=time(8, i))
        seed_result(env, call, TRANSCRIPT, confirmed(RFE="I received an RFE"))
    with env.factory() as db:
        out = reader.priority_cases(db, start=date(2026, 10, 1), end=date(2026, 10, 31), form_types=("RFE",), limit=2)
    assert out["RFE"]["total"] == 3 and out["RFE"]["truncated"] and len(out["RFE"]["rows"]) == 2


# ---------------------------------------------------------------- HTTP access and filters
@pytest.fixture
def client(env, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.flintdesk.priority_cases import router
    from storage.db import get_db
    from utils.jwt import create_access_token

    app = FastAPI()
    app.include_router(router)

    def _db():
        with env.factory() as db:
            yield db

    app.dependency_overrides[get_db] = _db
    test_client = TestClient(app)
    test_client.token = lambda role: {"Authorization": "Bearer " + create_access_token({"role": role, "sub": "u", "email": "u@x"})}
    return test_client


QUERY = "?start_date=2026-10-01&end_date=2026-10-31"


def test_api_requires_login_and_an_authorised_role(client, env):
    assert client.get("/flintdesk/priority-cases" + QUERY).status_code == 401
    assert client.get("/flintdesk/priority-cases" + QUERY, headers={"Authorization": "Bearer nope"}).status_code == 401
    for role in ("agent", "viewer", ""):
        assert client.get("/flintdesk/priority-cases" + QUERY, headers=client.token(role)).status_code == 403
    for role in ("admin", "team_lead", "subject_matter_expert", "sme"):
        assert client.get("/flintdesk/priority-cases" + QUERY, headers=client.token(role)).status_code == 200


def test_api_filters_by_form_type_in_the_backend_and_validates_input(client, env):
    call = env.call(OCT, TRANSCRIPT)
    seed_result(env, call, TRANSCRIPT, confirmed(RFE="I received an RFE", **{"I_485": "RFE for my I-485"}))
    headers = client.token("team_lead")
    everything = client.get("/flintdesk/priority-cases" + QUERY, headers=headers).json()
    assert set(everything["forms"]) == set(pc.FORMS) and everything["groups"] == {"high": ["RFE", "I-130", "I-485"], "other": ["H4", "H4EAD"]}
    assert everything["forms"]["RFE"]["total"] == 1 and everything["coverage"]["calls"] == 1
    only = client.get("/flintdesk/priority-cases" + QUERY + "&form_type=I-485", headers=headers).json()
    assert list(only["forms"]) == ["I-485"] and only["groups"] == {"high": ["I-485"], "other": []}
    for spelling in ("i485", "I 485", "i-485"):
        assert list(client.get("/flintdesk/priority-cases" + QUERY + f"&form_type={spelling}", headers=headers).json()["forms"]) == ["I-485"]
    assert list(client.get("/flintdesk/priority-cases" + QUERY + "&form_type=h4ead", headers=headers).json()["forms"]) == ["H4EAD"]
    assert client.get("/flintdesk/priority-cases" + QUERY + "&form_type=I-751", headers=headers).status_code == 422
    assert client.get("/flintdesk/priority-cases?start_date=2026-10-31&end_date=2026-10-01", headers=headers).status_code == 400
    assert client.get("/flintdesk/priority-cases?start_date=2026-01-01&end_date=2026-10-01", headers=headers).status_code == 400
    assert client.get("/flintdesk/priority-cases" + QUERY + "&result=pending", headers=headers).status_code == 422


def test_opening_the_api_never_calls_the_model(client, env, monkeypatch):
    from services.Processing_pipeline import fd_llm_node

    def boom(*a, **k):
        raise AssertionError("the Priority Cases API must never call the model")

    monkeypatch.setattr(fd_llm_node.FDLLM, "_post_with_retry", boom)
    env.call(OCT, TRANSCRIPT)
    assert client.get("/flintdesk/priority-cases" + QUERY, headers=client.token("admin")).status_code == 200


# ---------------------------------------------------------------- migration and worker wiring
def test_migration_seeds_the_prompt_once_and_never_overwrites_an_edit(env, monkeypatch):
    module = import_module("migrations.074_priority_cases")
    monkeypatch.setattr(module, "engine", env.engine)
    with env.engine.begin() as conn:
        seeded = conn.execute(text("SELECT version, is_active, updated_by, system_prompt FROM fd_prompts WHERE prompt_key = 'priority_case_classification'")).all()
    assert len(seeded) == 1 and seeded[0][:3] == (1, True, "seed")
    for form in ("RFE", "I-130", "I-485", "H4", "H4EAD", "uncertain", "confirmed"):
        assert form in seeded[0][3]
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE fd_prompts SET system_prompt = 'edited by admin' WHERE prompt_key = 'priority_case_classification'"))
    module.migrate_priority_cases()                                   # re-running is a no-op
    with env.engine.begin() as conn:
        assert conn.execute(text("SELECT system_prompt FROM fd_prompts WHERE prompt_key = 'priority_case_classification'")).scalar() == "edited by admin"
        assert conn.execute(text("SELECT count(*) FROM fd_prompts WHERE prompt_key = 'priority_case_classification'")).scalar() == 1


def test_the_seeded_prompt_drives_the_request(env, monkeypatch):
    from services import fd_prompt_store
    monkeypatch.setattr(fd_prompt_store, "engine", env.engine)
    prompt = fd_prompt_store.get_active_prompt("priority_case_classification")
    assert prompt and prompt["version"] == 1
    messages = pc.build_messages(prompt, TRANSCRIPT)
    assert TRANSCRIPT in messages[1]["content"] and "{{transcript}}" not in messages[1]["content"]
    assert pc.OUTPUT_CONTRACT in messages[0]["content"]


def test_deleting_a_call_removes_its_priority_row(env):
    call = env.call(OCT, TRANSCRIPT)
    env.run(StubLLM(answer()))
    with env.engine.begin() as conn:
        conn.execute(text("DELETE FROM calls WHERE call_id = CAST(:i AS uuid)"), {"i": call})
    assert env.row(call) is None
