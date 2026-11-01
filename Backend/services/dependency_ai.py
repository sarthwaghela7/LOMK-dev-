"""AI read of who an OPEN call's pending work depends on (Dashboard: Open Call Aging cards, one agent selected).

For every open call the model reads the transcript and the agent's Self Action Item and answers one question:
who has to act next?

    agent               the call's own agent (the one the call belongs to) still owes the next step
    other_agent         the next step needs another agent / team member / team lead to act or decide
    client_or_external  the call is waiting on the client, an attorney or another outside party
    none                nothing is pending

Everything the model returns is untrusted: only these four values are ever stored, anything else is discarded and the
call is simply left unanalysed (never guessed). Results are saved per call inside the existing JSONB column
call_analysis_results.fd_stage2_ai_meta under the key "dependency_ai" (no schema change) together with a hash of the
inputs, so a call is analysed again only when its transcript or Self Action Item changes. The Dashboard never calls the
model: it counts saved results. Analysis runs in a background job that is OFF unless OPEN_CALL_DEPENDENCY_AI_ENABLED.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

logger = logging.getLogger("flintdesk.dependency_ai")

META_KEY = "dependency_ai"
VALUES = ("agent", "other_agent", "client_or_external", "none")
MAX_TRANSCRIPT_CHARS = 12_000
MAX_ACTION_ITEM_CHARS = 2_000
LOOKBACK_DAYS = 92                    # the Dashboard never reads further back than this
# The AI reads calls from this date onward only (older open calls are never analysed, so they are never "waiting").
# Override with OPEN_CALL_DEPENDENCY_AI_SINCE=YYYY-MM-DD.
DEFAULT_AI_START = date(2026, 10, 14)
CLOSING_STATUSES = ("dropped", "success ge", "success pc", "success - ge", "success - pc")

# ---- cost and F3 protection ------------------------------------------------------------------------------------
MIN_AGE_DAYS = 3                      # the Dashboard cards only count calls open 3+ days, so younger calls are never analysed
BATCH_LIMIT = 8                       # calls per 5-minute run
MAX_ATTEMPTS = 3                      # a call whose inputs have not changed is tried at most this many times ...
RETRY_AFTER_HOURS = 6                 # ... and not more often than this
REQUESTS_PER_MINUTE = 6               # this job's own request budget (F3 has its own, much larger one)
F3_BUSY_WINDOW_MINUTES = 30           # a transcribed call F3 has not processed yet, registered this recently, means F3 is working
REQUEST_TIMEOUT_SECONDS = 60

SYSTEM_PROMPT = (
    "You help a law-office front desk. For ONE phone call you decide who has to act next on the still-open matter. "
    "Read the call transcript and the agent's Self Action Item (the agent's own to-do for this call). "
    "Answer with exactly one value for depends_on:\n"
    '- "agent": the next step is something THE CALL\'S AGENT (named below) must do himself/herself, e.g. call the client back, '
    "send the email or document, prepare or check something, make the decision.\n"
    '- "other_agent": the next step needs ANOTHER agent, team lead, subject-matter expert or colleague to act, answer or decide '
    "before this agent can continue (a different front-desk or office team member is named or clearly meant).\n"
    '- "client_or_external": the call is waiting on the client, an attorney, a court or another outside party.\n'
    '- "none": nothing further is pending.\n'
    "Use only what the transcript and the Self Action Item say; never invent facts. If the call's own agent must act first, "
    'prefer "agent". Return ONLY a JSON object: {"depends_on": "<one of the four values>", "reason": "<one short factual sentence>"}.'
)


def ai_start_date() -> date:
    try:
        return date.fromisoformat(os.getenv("OPEN_CALL_DEPENDENCY_AI_SINCE", "").strip() or DEFAULT_AI_START.isoformat())
    except ValueError:
        return DEFAULT_AI_START


# ---------------------------------------------------------------- inputs / validation
def input_hash(transcript, self_action_item) -> str:
    """md5 of the exact inputs; the same expression runs in SQL (see _candidates), so staleness is decided in the database."""
    return hashlib.md5(f"{transcript or ''}|{self_action_item or ''}".encode("utf-8")).hexdigest()


def build_messages(*, transcript, self_action_item, agent_name, other_agent_names) -> list[dict]:
    transcript = (transcript or "").strip()[:MAX_TRANSCRIPT_CHARS]
    action = (self_action_item or "").strip()[:MAX_ACTION_ITEM_CHARS] or "(none written)"
    others = ", ".join(sorted({n for n in other_agent_names if n and n != agent_name})[:30]) or "(none listed)"
    user = (
        f"The call's agent: {agent_name or '(unknown)'}\n"
        f"Other front-desk agents in the office: {others}\n\n"
        f"Self Action Item:\n{action}\n\n"
        f"Call transcript:\n{transcript}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def parse_result(content) -> dict | None:
    """The validated answer, or None for anything that is not exactly one of the four allowed values."""
    if not isinstance(content, str):
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.I | re.M).strip()
    try:
        data = json.loads(cleaned)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    value = str(data.get("depends_on") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if value not in VALUES:
        return None
    reason = data.get("reason")
    return {"depends_on": value, "reason": (str(reason).strip()[:300] if isinstance(reason, str) and reason.strip() else None)}


def classify(llm, *, transcript, self_action_item, agent_name, other_agent_names, attempts=2) -> dict | None:
    """One validated classification, or None (never a guess). `llm` is an FDLLM-like object with _post_with_retry()."""
    if not (transcript or "").strip():
        return None
    messages = build_messages(transcript=transcript, self_action_item=self_action_item, agent_name=agent_name,
                              other_agent_names=other_agent_names)
    for attempt in range(attempts):
        response = llm._post_with_retry({"model": llm.model, "messages": messages, "temperature": 0,
                                         "response_format": {"type": "json_object"}})
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            content = None
        result = parse_result(content)
        if result is not None:
            return result
        logger.warning("dependency_ai: unusable model output (attempt %s/%s)", attempt + 1, attempts)
    return None


# ---------------------------------------------------------------- background batch
_CANDIDATES = text("""
    SELECT a.id AS analysis_id, c.call_id::text AS call_id, c.transcript, a.fd_m_self_action_item,
           a.front_desk_person_name, md5(coalesce(c.transcript, '') || '|' || coalesce(a.fd_m_self_action_item, '')) AS h
    FROM call_analysis_results a
    JOIN calls c ON c.call_id::text = a.call_id::text
    WHERE c.call_date >= :since
      AND c.call_date <= :newest
      AND coalesce(btrim(c.transcript), '') <> ''
      AND lower(btrim(coalesce(a.fd_m_case_status_change, ''))) NOT IN ('dropped', 'success ge', 'success pc', 'success - ge', 'success - pc')
      AND NOT EXISTS (SELECT 1 FROM fd_case_closure_links l WHERE l.analysis_id = a.id AND l.reverted_at IS NULL)
      AND coalesce(a.fd_stage2_ai_meta -> 'dependency_ai' ->> 'input_hash', '')
          <> md5(coalesce(c.transcript, '') || '|' || coalesce(a.fd_m_self_action_item, ''))
      AND NOT (
            coalesce(a.fd_stage2_ai_meta -> 'dependency_ai_failed' ->> 'input_hash', '')
                = md5(coalesce(c.transcript, '') || '|' || coalesce(a.fd_m_self_action_item, ''))
            AND (coalesce((a.fd_stage2_ai_meta -> 'dependency_ai_failed' ->> 'attempts')::int, 0) >= :max_attempts
                 OR (a.fd_stage2_ai_meta -> 'dependency_ai_failed' ->> 'last_at')::timestamptz
                    > now() - make_interval(hours => :retry_hours))
          )
    ORDER BY c.call_date DESC, c.call_time DESC
    LIMIT :n
""")


def _other_agent_names(db) -> list[str]:
    rows = db.execute(text("SELECT name FROM fd_users WHERE is_active AND lower(role) = 'agent' ORDER BY name LIMIT 60")).all()
    return [r[0] for r in rows if r[0]]


def store_result(db, analysis_id, result: dict, source_hash: str, model: str | None) -> None:
    """Merge the saved answer into fd_stage2_ai_meta without touching the other keys (Stage 2's own values)."""
    payload = {**result, "input_hash": source_hash, "analysed_at": datetime.now(timezone.utc).isoformat(), "model": model}
    db.execute(text("""
        UPDATE call_analysis_results
        SET fd_stage2_ai_meta = jsonb_set(coalesce(fd_stage2_ai_meta, '{}'::jsonb), '{dependency_ai}', CAST(:v AS JSONB), true)
        WHERE id = :id
    """), {"v": json.dumps(payload), "id": analysis_id})


class ProviderBusy(Exception):
    """The provider asked us to slow down (429 / 5xx / credits). The batch stops and F3's own limiter is never touched."""


class DependencyLLM:
    """This job's own client: its own request budget, one attempt per request, and no shared state with F3.

    F3's FDLLM._post_with_retry shrinks the shared rate limiter on every 429 and retries with long back-offs; using it here
    could slow F3 down. This client only reads F3's provider circuit (never writes it) and gives up on the first 429.
    """

    def __init__(self, model: str | None = None):
        from utils import config
        from utils.rate_limiter import TokenBucketRateLimiter
        if not config.LLM_API_KEY or not config.LLM_URL:
            raise ValueError("LLM_API_KEY / LLM_URL not configured")
        self.api_key, self.url = config.LLM_API_KEY, config.LLM_URL
        self.model = (os.getenv("OPEN_CALL_DEPENDENCY_AI_MODEL", "").strip() or model or config.LLM_MODEL)
        self._limiter = TokenBucketRateLimiter(REQUESTS_PER_MINUTE)

    def _post_with_retry(self, payload):
        import time
        import requests
        from services.Processing_pipeline import fd_llm_node
        if fd_llm_node._provider_circuit["unavailable_until"] > time.monotonic():
            raise ProviderBusy("provider credits unavailable (F3 circuit open)")
        self._limiter.acquire()
        response = requests.post(self.url, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                                 json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderBusy(f"provider returned {response.status_code}")
        response.raise_for_status()
        return response


def f3_is_busy(db) -> bool:
    """True while F3 (the main call analysis) has recently transcribed calls it has not processed yet. This job then steps aside."""
    return bool(db.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM calls
            WHERE call_date >= (now() AT TIME ZONE 'America/Chicago')::date - 1
              AND coalesce(btrim(transcript), '') <> ''
              AND coalesce(fd_processing_status, 'not processed') <> 'processed'
              AND registered_at > now() - make_interval(mins => :m))
    """), {"m": F3_BUSY_WINDOW_MINUTES}).scalar())


def record_failure(db, analysis_id, source_hash: str) -> None:
    """Remember a failed or unusable attempt (same JSONB column, sibling key) so the call is retried a few times, not forever."""
    db.execute(text("""
        UPDATE call_analysis_results
        SET fd_stage2_ai_meta = jsonb_set(coalesce(fd_stage2_ai_meta, '{}'::jsonb), '{dependency_ai_failed}',
            jsonb_build_object('input_hash', CAST(:h AS TEXT),
                               'attempts', CASE WHEN fd_stage2_ai_meta -> 'dependency_ai_failed' ->> 'input_hash' = CAST(:h AS TEXT)
                                                THEN coalesce((fd_stage2_ai_meta -> 'dependency_ai_failed' ->> 'attempts')::int, 0) + 1 ELSE 1 END,
                               'last_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')), true)
        WHERE id = :id
    """), {"h": source_hash, "id": analysis_id})


def run_batch(db, llm, *, limit=BATCH_LIMIT, today: date | None = None) -> dict:
    """Analyse up to `limit` open calls (3+ days old) that have no (or an out-of-date) saved answer. Never raises per call.

    Steps aside while F3 is busy, stops at the first provider slow-down, and gives up on a call after MAX_ATTEMPTS.
    """
    from services.case_closure import default_owner_resolver
    from services.open_call_aging import central_today
    today = today or central_today()
    since = max(today - timedelta(days=LOOKBACK_DAYS), ai_start_date())
    counts = {"candidates": 0, "saved": 0, "unusable": 0, "failed": 0, "yielded_to_f3": False, "provider_busy": False}
    if f3_is_busy(db):
        counts["yielded_to_f3"] = True
        return counts
    rows = [dict(r._mapping) for r in db.execute(_CANDIDATES, {"since": since, "newest": today - timedelta(days=MIN_AGE_DAYS), "n": limit,
                                                              "max_attempts": MAX_ATTEMPTS, "retry_hours": RETRY_AFTER_HOURS})]
    counts["candidates"] = len(rows)
    if not rows:
        return counts
    owners = default_owner_resolver(db, rows)
    others = _other_agent_names(db)
    for index, row in enumerate(rows):
        if index and f3_is_busy(db):
            counts["yielded_to_f3"] = True
            break
        agent = (owners.get(row["analysis_id"]) or (None, None))[1] or row.get("front_desk_person_name")
        try:
            result = classify(llm, transcript=row["transcript"], self_action_item=row["fd_m_self_action_item"],
                              agent_name=agent, other_agent_names=others)
        except ProviderBusy as exc:
            logger.warning("dependency_ai: provider busy, stopping this run (%s)", exc)
            counts["provider_busy"] = True
            db.rollback()
            break
        except Exception:  # noqa: BLE001 - one bad call (timeout, bad response) must not stop the batch
            logger.exception("dependency_ai: analysis failed for call %s", row["call_id"])
            counts["failed"] += 1
            db.rollback()
            record_failure(db, row["analysis_id"], row["h"])
            db.commit()
            continue
        if result is None:
            counts["unusable"] += 1
            record_failure(db, row["analysis_id"], row["h"])
            db.commit()
            continue
        store_result(db, row["analysis_id"], result, row["h"], getattr(llm, "model", None))
        db.commit()
        counts["saved"] += 1
    return counts
