"""Priority Cases: which calls discuss RFE, I-130, I-485, H4 or H4EAD.

One structured request per transcript evaluates all five forms (no per-form requests). Results are stored in
call_priority_analysis (migration 074), keyed by call, with the sha256 of the transcript and the fd_prompts version that
produced them, so an unchanged transcript is never paid for twice. The Priority Cases page and API only read these rows;
nothing here runs when a page loads.

This module never writes to calls or call_analysis_results, and does not use F3's analysis_jobs or fd_processing_status:
a priority-analysis failure cannot change the Calls page analysis, and F3 failures do not block this.

Cost controls, in order:
  1. calls without a transcript are skipped (no STT is ever triggered here);
  2. NO keyword gate: every call with a usable transcript is sent to the model, because a call can concern a form without
     ever naming it. The lexical helper below is a diagnostic hint only; it never skips a request and never decides a
     status. The only "no request" outcome left is a call the Calls page does not list (Missed-Calls / routing legs);
  3. unchanged transcript + same prompt version + completed result -> skipped;
  4. permanent/validation failures stop after MAX_ATTEMPTS (retried only after RETRY_AFTER_MINUTES, and again only if the
     transcript or prompt version changes).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import date, timedelta

from sqlalchemy import text

logger = logging.getLogger("flintdesk.priority_cases")

FORMS = ("RFE", "I-130", "I-485", "H4", "H4EAD")
HIGH_PRIORITY_FORMS = ("RFE", "I-130", "I-485")
OTHER_PRIORITY_FORMS = ("H4", "H4EAD")
STATUSES = ("confirmed", "not_confirmed", "uncertain")
CONFIDENCES = ("high", "medium", "low")
PROMPT_KEY = "priority_case_classification"

# Bump when a rule that produced stored NON-model rows changes. Rows with analysis_source = 'llm' are not affected by
# the revision (their validity is the transcript hash + prompt version), so a bump never repays for model answers.
# Rev "1" = the keyword pre-filter could store "nothing found" without a request; rev "2" removed that gate, so those
# rows are re-queued (see CANDIDATES_SQL).
RESULT_REV = "2"
MAX_TRANSCRIPT_CHARS = 14_000
MAX_EVIDENCE_CHARS = 300
MAX_REASON_CHARS = 300

# ---- retry / lease / batching -------------------------------------------------------------------------------------
MAX_ATTEMPTS = 3
RETRY_AFTER_MINUTES = 30
LEASE_MINUTES = 15
WORKER_BATCH_LIMIT = int(os.getenv("PRIORITY_CASES_BATCH_LIMIT", "10") or 10)
WORKER_LOOKBACK_DAYS = int(os.getenv("PRIORITY_CASES_LOOKBACK_DAYS", "3") or 3)
# The automatic worker starts at deployment day (Central) so deploying never triggers history; history is the explicit backfill.
DEFAULT_START = date(2026, 10, 20)

OUTPUT_CONTRACT = (
    "Return ONLY one JSON object, with no markdown fences and no text outside it, in exactly this shape:\n"
    '{"forms": {"RFE": F, "I-130": F, "I-485": F, "H4": F, "H4EAD": F}}\n'
    'where each F is {"status": "confirmed" | "not_confirmed" | "uncertain", "evidence": string | null, '
    '"reason": string | null, "confidence": "high" | "medium" | "low" | null}.\n'
    "All five keys are required. evidence is a short excerpt copied word for word from the transcript (required for "
    "confirmed and uncertain, null for not_confirmed)."
)


def enabled() -> bool:
    """The background worker runs unless PRIORITY_CASES_ENABLED is explicitly false."""
    return os.getenv("PRIORITY_CASES_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def start_date() -> date:
    """Earliest call date the automatic worker will look at (PRIORITY_CASES_SINCE=YYYY-MM-DD; default: deployment day)."""
    try:
        return date.fromisoformat(os.getenv("PRIORITY_CASES_SINCE", "").strip() or DEFAULT_START.isoformat())
    except ValueError:
        return DEFAULT_START


# =====================================================================================================================
# Keyword hints (diagnostics only: they never skip a model request and never decide a status)
# =====================================================================================================================
_NUM = {"130": r"(?:130|one\s*thirty|1\s*30|thirty)", "485": r"(?:485|four\s*eighty[\s-]*five|4\s*85|four\s*eight\s*five)"}
_I = r"(?<![a-z0-9])(?:i|eye|ai)"
_SEP = r"[\s.\-_/]*"
_H4_CORE = r"(?<![a-z0-9])h" + _SEP + r"(?:4|four|for)"
_H4 = _H4_CORE + r"(?![0-9a-z])"
_CANDIDATES = {
    "RFE": re.compile(
        r"(?<![a-z0-9])r" + _SEP + r"f" + _SEP + r"e(?![a-z0-9])"
        r"|request(?:s)?\s+(?:for|of)\s+(?:additional\s+)?evidence|\brfes?\b|evidence\s+request"),
    "I-130": re.compile(
        _I + _SEP + _NUM["130"] + r"(?![0-9])|alien\s+relative|family[\s-]*(?:based\s+)?petition"
        r"|petition\s+for\s+(?:my\s+)?(?:wife|husband|spouse|parent|mother|father|son|daughter|child|fianc)"),
    "I-485": re.compile(
        _I + _SEP + _NUM["485"] + r"(?![0-9])|green\s*card|adjust(?:ment)?\s+of\s+status|permanent\s+residen"),
    "H4": re.compile(_H4 + r"|dependent\s+visa|dependent\s+status|spouse\s+of\s+(?:an?\s+)?h"),
    "H4EAD": re.compile(
        _H4_CORE + _SEP + r"(?:e" + _SEP + r"a" + _SEP + r"d|ead)|(?<![a-z0-9])e" + _SEP + r"a" + _SEP + r"d(?![a-z0-9])"
        r"|employment\s+authori[sz]ation|work\s+(?:permit|authori[sz]ation)|\bi[\s-]*765\b"),
}
# Hint words from F3's own case type / enquiry text also widen the candidate set (never narrow it).
_HINT_FIELDS = ("fd_a_case_type", "case_type", "enquiry_details")


def candidate_forms(transcript: str | None, hints: str | None = None) -> set[str]:
    """Forms the text names (spelling-tolerant). Diagnostic only: an empty set does NOT mean no request is needed."""
    haystack = f"{transcript or ''}\n{hints or ''}".lower()
    return {form for form, pattern in _CANDIDATES.items() if pattern.search(haystack)}


# =====================================================================================================================
# Prompt and response validation
# =====================================================================================================================
def transcript_hash(transcript: str | None) -> str:
    """sha256 of the exact stored transcript (the same expression runs in SQL: sha256(convert_to(transcript, 'UTF8')))."""
    return hashlib.sha256((transcript or "").encode("utf-8")).hexdigest()


def build_messages(prompt: dict, transcript: str) -> list[dict]:
    """The model request: the active fd_prompts text + the fixed output contract + only the transcript (no metadata)."""
    body = (transcript or "").strip()[:MAX_TRANSCRIPT_CHARS]
    user = prompt["user_prompt_template"].replace("{{transcript}}", body)
    return [{"role": "system", "content": f"{prompt['system_prompt'].strip()}\n\n{OUTPUT_CONTRACT}"},
            {"role": "user", "content": user}]


_FORM_ALIASES = {re.sub(r"[^a-z0-9]", "", name.lower()): name for name in FORMS}
_FORM_ALIASES.update({"h4ead": "H4EAD", "requestforevidence": "RFE"})


def _form_name(key) -> str | None:
    return _FORM_ALIASES.get(re.sub(r"[^a-z0-9]", "", str(key or "").lower()))


def _squash(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def evidence_in_transcript(evidence: str, transcript: str) -> bool:
    """True when the quoted evidence really occurs in the transcript (case, spacing and punctuation ignored)."""
    needle = _squash(evidence)
    return bool(needle) and needle in _squash(transcript)


class InvalidModelOutput(ValueError):
    """The response is not usable (malformed JSON, missing form, invalid status). Never stored."""


def _clean_text(value, limit):
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:limit] or None


def parse_result(content, transcript: str) -> dict:
    """Validate a model answer into {"forms": {form: {status, evidence, reason, confidence}}}.

    Raises InvalidModelOutput for anything structurally wrong (so it is retried / counted as a failure, never saved).
    Content rules, applied deterministically after the model answers:
      * confirmed without evidence            -> uncertain (nothing to show a reviewer);
      * evidence not found in the transcript  -> uncertain (possible invention; kept for review, never confirmed);
      * evidence for not_confirmed            -> dropped.
    """
    if not isinstance(content, str) or not content.strip():
        raise InvalidModelOutput("empty response")
    cleaned = re.sub(r"^\s*```(?:json)?|```\s*$", "", content.strip(), flags=re.I).strip()
    try:
        data = json.loads(cleaned)
    except ValueError as exc:
        raise InvalidModelOutput("response is not valid JSON") from exc
    forms_in = data.get("forms") if isinstance(data, dict) else None
    if not isinstance(forms_in, dict):
        raise InvalidModelOutput("missing 'forms' object")
    by_name = {}
    for key, value in forms_in.items():
        name = _form_name(key)
        if name and name not in by_name:
            by_name[name] = value
    missing = [form for form in FORMS if form not in by_name]
    if missing:
        raise InvalidModelOutput(f"missing forms: {', '.join(missing)}")
    forms_out = {}
    for form in FORMS:
        entry = by_name[form]
        if not isinstance(entry, dict):
            raise InvalidModelOutput(f"{form}: entry is not an object")
        status = str(entry.get("status") or "").strip().lower().replace("-", "_").replace(" ", "_")
        if status not in STATUSES:
            raise InvalidModelOutput(f"{form}: invalid status")
        evidence = _clean_text(entry.get("evidence"), MAX_EVIDENCE_CHARS)
        reason = _clean_text(entry.get("reason"), MAX_REASON_CHARS)
        confidence = str(entry.get("confidence") or "").strip().lower()
        confidence = confidence if confidence in CONFIDENCES else None
        if status == "not_confirmed":
            evidence = reason = confidence = None
        else:
            if status == "confirmed" and (evidence is None or not evidence_in_transcript(evidence, transcript)):
                status = "uncertain"
                reason = (reason + " " if reason else "") + (
                    "[Downgraded: no verifiable transcript evidence.]")
                reason = reason[:MAX_REASON_CHARS + 60]
            elif status == "uncertain" and evidence is not None and not evidence_in_transcript(evidence, transcript):
                evidence = None   # never keep an excerpt that is not in the transcript
        forms_out[form] = {"status": status, "evidence": evidence, "reason": reason, "confidence": confidence}
    return {"forms": forms_out}


def empty_result() -> dict:
    return {"forms": {form: {"status": "not_confirmed", "evidence": None, "reason": None, "confidence": None}
                      for form in FORMS}}


def forms_with_status(result: dict, status: str) -> list[str]:
    return [form for form in FORMS if (result.get("forms", {}).get(form) or {}).get("status") == status]


def classify(llm, prompt: dict, transcript: str, attempts: int = 2) -> tuple[dict, dict]:
    """(validated result, usage) from ONE structured request (a second only if the first answer is unusable).

    `llm` is an FDLLM-like object with .model and ._post_with_retry() (F3's client: shared rate limit, 429 back-off and the
    credit-exhausted circuit breaker). Provider errors propagate to the caller; unusable output raises InvalidModelOutput
    after `attempts` answers. Usage sums every request made.
    """
    messages = build_messages(prompt, transcript)
    usage = {"input_tokens": 0, "output_tokens": 0}
    last_error = None
    for _ in range(max(1, attempts)):
        response = llm._post_with_retry({"model": llm.model, "messages": messages, "temperature": 0,
                                         "response_format": {"type": "json_object"}})
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            body, content = {}, None
        token_usage = body.get("usage") if isinstance(body, dict) else None
        if isinstance(token_usage, dict):
            usage["input_tokens"] += int(token_usage.get("prompt_tokens") or 0)
            usage["output_tokens"] += int(token_usage.get("completion_tokens") or 0)
        try:
            return parse_result(content, transcript), usage
        except InvalidModelOutput as exc:
            last_error = exc
            logger.warning("priority_cases: unusable model output (%s)", exc)
    raise last_error or InvalidModelOutput("no usable response")


# =====================================================================================================================
# Persistence: claim -> store / fail / release
# =====================================================================================================================
_THIS_HASH = "encode(sha256(convert_to(c.transcript, 'UTF8')), 'hex')"

_STALE_CLAUSES = """
           OR p.prompt_version IS DISTINCT FROM :version
           OR (p.analysis_status = 'completed' AND p.analysis_source IS DISTINCT FROM 'llm'
               AND p.result ->> 'rev' IS DISTINCT FROM :rev)"""


def _candidates_sql(reanalyse: bool):
    # reanalyse=False (the automatic worker): a call that already has a completed result is never picked up again just
    # because the prompt version or result revision moved on; only the explicit backfill passes reanalyse=True.
    return text(f"""
    SELECT c.call_id::text AS call_id, c.call_date, c.call_time, c.transcript,
           concat_ws(' ', a.fd_a_case_type, a.case_type, a.enquiry_details) AS hints
    FROM calls c
    LEFT JOIN call_priority_analysis p ON p.call_id = c.call_id
    LEFT JOIN call_analysis_results a ON a.call_id::text = c.call_id::text
    WHERE c.call_date BETWEEN :start AND :end
      AND coalesce(btrim(c.transcript), '') <> ''
      AND (   p.call_id IS NULL
           OR p.analysis_status = 'pending'
           OR p.transcript_hash IS DISTINCT FROM {_THIS_HASH}{_STALE_CLAUSES if reanalyse else ""}
           OR (p.analysis_status = 'processing' AND coalesce(p.claimed_at, '-infinity') < now() - make_interval(mins => :lease))
           OR (p.analysis_status = 'failed' AND p.attempts < :max_attempts
               AND p.last_attempt_at < now() - make_interval(mins => :retry)))
      AND NOT (coalesce(p.analysis_status, '') = 'processing'
               AND coalesce(p.claimed_at, '-infinity') >= now() - make_interval(mins => :lease))
    ORDER BY c.call_date ASC, c.call_time ASC
    LIMIT :n
""")


CANDIDATES_SQL = _candidates_sql(True)
NEW_CALLS_ONLY_SQL = _candidates_sql(False)

CLAIM_SQL = text("""
    INSERT INTO call_priority_analysis AS p
        (call_id, analysis_status, transcript_hash, prompt_version, attempts, claimed_at, last_attempt_at, updated_at)
    VALUES (CAST(:call_id AS uuid), 'processing', :hash, :version, 1, now(), now(), now())
    ON CONFLICT (call_id) DO UPDATE SET
        analysis_status = 'processing',
        attempts = CASE WHEN p.transcript_hash = EXCLUDED.transcript_hash AND p.prompt_version = EXCLUDED.prompt_version
                        THEN p.attempts + 1 ELSE 1 END,
        transcript_hash = EXCLUDED.transcript_hash, prompt_version = EXCLUDED.prompt_version,
        claimed_at = now(), last_attempt_at = now(), error = NULL, updated_at = now()
    WHERE NOT (p.analysis_status = 'processing' AND coalesce(p.claimed_at, '-infinity') >= now() - make_interval(mins => :lease))
      AND NOT (p.analysis_status = 'completed' AND p.transcript_hash = EXCLUDED.transcript_hash
               AND p.prompt_version = EXCLUDED.prompt_version
               AND (p.analysis_source = 'llm' OR coalesce(p.result ->> 'rev', '') = :rev))
    RETURNING attempts
""")

STORE_SQL = text("""
    UPDATE call_priority_analysis SET
        analysis_status = 'completed', analysis_source = :source, result = CAST(:result AS JSONB),
        confirmed_forms = CAST(:confirmed AS TEXT[]), uncertain_forms = CAST(:uncertain AS TEXT[]),
        model = :model, input_tokens = :tin, output_tokens = :tout, error = NULL,
        claimed_at = NULL, analyzed_at = now(), updated_at = now()
    WHERE call_id = CAST(:call_id AS uuid) AND analysis_status = 'processing' AND transcript_hash = :hash
""")

FAIL_SQL = text("""
    UPDATE call_priority_analysis SET analysis_status = 'failed', error = :error, claimed_at = NULL, updated_at = now()
    WHERE call_id = CAST(:call_id AS uuid) AND analysis_status = 'processing'
""")

RELEASE_SQL = text("""
    UPDATE call_priority_analysis SET analysis_status = 'pending', claimed_at = NULL,
        attempts = greatest(attempts - 1, 0), updated_at = now()
    WHERE call_id = CAST(:call_id AS uuid) AND analysis_status = 'processing'
""")


def _pg_array(values) -> str:
    return "{" + ",".join(f'"{v}"' for v in values) + "}"


def candidates(db, *, start: date, end: date, version: int, limit: int, reanalyse: bool = True) -> list[dict]:
    rows = db.execute(CANDIDATES_SQL if reanalyse else NEW_CALLS_ONLY_SQL, {"start": start, "end": end, "version": version, "rev": RESULT_REV, "lease": LEASE_MINUTES,
                                       "max_attempts": MAX_ATTEMPTS, "retry": RETRY_AFTER_MINUTES, "n": limit})
    return [dict(row._mapping) for row in rows]


def claim(db, call_id: str, transcript: str, version: int) -> int | None:
    """Attempt number when this process now owns the analysis, else None (someone else has it, or it is already done)."""
    row = db.execute(CLAIM_SQL, {"call_id": call_id, "hash": transcript_hash(transcript), "version": version,
                                 "lease": LEASE_MINUTES, "rev": RESULT_REV}).first()
    db.commit()
    return row[0] if row else None


def store(db, call_id: str, transcript: str, result: dict, *, source: str, model: str | None, usage: dict | None) -> bool:
    result = {**result, "rev": RESULT_REV}
    usage = usage or {}
    done = db.execute(STORE_SQL, {
        "call_id": call_id, "hash": transcript_hash(transcript), "source": source, "result": json.dumps(result),
        "confirmed": _pg_array(forms_with_status(result, "confirmed")),
        "uncertain": _pg_array(forms_with_status(result, "uncertain")),
        "model": model, "tin": usage.get("input_tokens"), "tout": usage.get("output_tokens")}).rowcount
    db.commit()
    return bool(done)


def fail(db, call_id: str, error: str) -> None:
    db.execute(FAIL_SQL, {"call_id": call_id, "error": error[:500]})
    db.commit()


def release(db, call_id: str) -> None:
    """Give the claim back unchanged (provider slow-down): the call is simply picked up again later."""
    db.execute(RELEASE_SQL, {"call_id": call_id})
    db.commit()


# =====================================================================================================================
# Batch runner (used by the scheduler job AND ops/backfill_priority_cases.py)
# =====================================================================================================================
def active_prompt() -> dict | None:
    from services.fd_prompt_store import get_active_prompt
    return get_active_prompt(PROMPT_KEY)


def run_batch(session_factory, llm_factory, *, start: date, end: date, limit: int, prompt: dict | None = None,
              is_listed=None, should_stop=None, reanalyse: bool = False) -> dict:
    """Analyse up to `limit` calls whose stored result is missing or out of date. Never raises per call.

    llm_factory() is only invoked when a model request is actually needed (so a quiet minute costs nothing).
    is_listed(call_date) -> set[str] | None optionally limits paid requests to calls the Calls page lists.
    should_stop() is polled between calls (the scheduler yields to F3 with it).
    reanalyse=False (default, used by the worker): calls that already have a completed result are left alone even if the
    prompt version or result revision has changed. Only the explicit backfill passes reanalyse=True.
    """
    counts = {"eligible": 0, "analysed": 0, "matched": 0, "uncertain": 0, "not_listed": 0,
              "keyword_hint": 0, "no_keyword_hint": 0,
              "failed": 0, "claimed_elsewhere": 0, "provider_busy": False, "stopped": False,
              "input_tokens": 0, "output_tokens": 0, "llm_requests": 0, "prompt_version": None}
    prompt = prompt or active_prompt()
    if prompt is None:
        counts["error"] = "no active priority_case_classification prompt in fd_prompts; run migration 074"
        return counts
    version = prompt["version"]
    counts["prompt_version"] = version
    with session_factory() as db:
        rows = candidates(db, start=start, end=end, version=version, limit=limit, reanalyse=reanalyse)
    counts["eligible"] = len(rows)
    llm = None
    listed_cache: dict = {}
    from services.Processing_pipeline.fd_llm_node import LLMQuotaExhausted, LLMRateLimitExhausted
    for row in rows:
        if should_stop and should_stop():
            counts["stopped"] = True
            break
        call_id, transcript = row["call_id"], row["transcript"]
        if not (transcript or "").strip():   # defensive: candidates() already requires a non-blank transcript
            continue
        try:
            with session_factory() as db:
                if claim(db, call_id, transcript, version) is None:
                    counts["claimed_elsewhere"] += 1
                    continue
                # Diagnostic only (never a gate): how many transcripts would the old keyword filter have dropped?
                counts["keyword_hint" if candidate_forms(transcript, row.get("hints")) else "no_keyword_hint"] += 1
                if is_listed is not None:
                    day = row["call_date"]
                    if day not in listed_cache:
                        listed_cache[day] = is_listed(day)
                    listed = listed_cache[day]
                    if listed is not None and call_id not in listed:
                        # Missed-Calls/routing leg: not a Call Record, so no paid request; recorded as nothing found.
                        store(db, call_id, transcript, {**empty_result(), "prompt_version": version}, source="prefilter",
                              model=None, usage=None)
                        counts["not_listed"] += 1
                        continue
            # No database transaction is held during the model request.
            llm = llm or llm_factory()
            try:
                result, usage = classify(llm, prompt, transcript)
            except (LLMQuotaExhausted, LLMRateLimitExhausted) as exc:
                logger.warning("priority_cases: provider busy, stopping this run (%s)", type(exc).__name__)
                counts["provider_busy"] = True
                with session_factory() as db:
                    release(db, call_id)
                break
            except InvalidModelOutput as exc:
                counts["failed"] += 1
                with session_factory() as db:
                    fail(db, call_id, f"invalid model output: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - transport/HTTP error for this call
                counts["failed"] += 1
                with session_factory() as db:
                    fail(db, call_id, f"{type(exc).__name__}")
                logger.warning("priority_cases: request failed for %s (%s)", call_id, type(exc).__name__)
                continue
            counts["llm_requests"] += 1
            counts["input_tokens"] += usage["input_tokens"]
            counts["output_tokens"] += usage["output_tokens"]
            with session_factory() as db:
                store(db, call_id, transcript, {**result, "prompt_version": version}, source="llm",
                      model=getattr(llm, "model", None), usage=usage)
            counts["analysed"] += 1
            if forms_with_status(result, "confirmed"):
                counts["matched"] += 1
            if forms_with_status(result, "uncertain"):
                counts["uncertain"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad call must never stop the batch
            counts["failed"] += 1
            logger.warning("priority_cases: call %s failed (%s)", call_id, type(exc).__name__)
    return counts


def coverage(db, *, start: date, end: date, version: int | None) -> dict:
    """Backfill/ops view over [start, end]: how many calls have a transcript and what state each analysis is in."""
    row = db.execute(text(f"""
        SELECT count(*) FILTER (WHERE coalesce(btrim(c.transcript), '') = '') AS missing_transcript,
               count(*) FILTER (WHERE coalesce(btrim(c.transcript), '') <> '') AS with_transcript,
               count(*) FILTER (WHERE coalesce(btrim(c.transcript), '') <> '' AND p.analysis_status = 'completed'
                                 AND p.transcript_hash = {_THIS_HASH} AND p.prompt_version IS NOT DISTINCT FROM :version
                                 AND (p.analysis_source = 'llm' OR p.result ->> 'rev' = :rev)) AS up_to_date,
               count(*) FILTER (WHERE p.analysis_status = 'failed') AS failed,
               count(*) FILTER (WHERE p.analysis_status = 'completed' AND cardinality(p.confirmed_forms) > 0) AS with_confirmed,
               count(*) FILTER (WHERE p.analysis_status = 'completed' AND cardinality(p.uncertain_forms) > 0) AS with_uncertain,
               coalesce(sum(p.input_tokens), 0) AS input_tokens, coalesce(sum(p.output_tokens), 0) AS output_tokens,
               count(*) FILTER (WHERE p.analysis_source = 'llm') AS llm_analyses
        FROM calls c LEFT JOIN call_priority_analysis p ON p.call_id = c.call_id
        WHERE c.call_date BETWEEN :start AND :end
    """), {"start": start, "end": end, "version": version, "rev": RESULT_REV}).first()
    return {key: int(value) for key, value in row._mapping.items()}


def worker_window(today: date) -> tuple[date, date]:
    return max(start_date(), today - timedelta(days=WORKER_LOOKBACK_DAYS)), today
