"""
FlintDesk-specific LLM analysis node.

This module is intentionally separate from the existing AutoFlint LLM node.
It is used only by the FlintDesk Analyse workflow and generates the
fd_a_* fields for the FlintDesk AI analysis result.

Do not import or execute this module from the AutoFlint analysis pipeline.
"""

import json
import random
import re
import logging
import time

import requests

from utils.config import (
    LLM_API_KEY,
    LLM_MODEL,
    LLM_URL,
    OPENAI_RPM_LIMIT,
    F3_LLM_MAX_RETRIES,
    F3_LLM_BACKOFF_BASE_SECONDS,
    F3_LLM_BACKOFF_MAX_SECONDS,
)
from utils.rate_limiter import TokenBucketRateLimiter
from monitoring.recorder import record_llm_response

logger = logging.getLogger(__name__)

# Shared across every FDLLM instance/thread in this process so total request
# rate never exceeds the confirmed OpenAI RPM tier, no matter how many F3
# worker threads are running concurrently.
_RATE_LIMITER = TokenBucketRateLimiter(OPENAI_RPM_LIMIT)

# Process-wide count of 429s hit this run; f3.py logs/reports this per batch.
_rate_limit_hits_lock = __import__("threading").Lock()
_rate_limit_hits = {"count": 0}
_provider_circuit = {"unavailable_until": 0.0}
_PROVIDER_RECHECK_SECONDS = 300


def get_and_reset_rate_limit_hits():
    with _rate_limit_hits_lock:
        count = _rate_limit_hits["count"]
        _rate_limit_hits["count"] = 0
        return count


def get_current_rate_limiter_capacity():
    """Current effective RPM capacity, after any adaptive throttling."""
    return _RATE_LIMITER.capacity


class LLMRateLimitExhausted(Exception):
    """Raised when OpenAI kept returning 429 after all retries were used."""


class LLMQuotaExhausted(Exception):
    """Raised without repeated requests while provider credits are exhausted."""


FD_AI_FIELDS = [
    "fd_a_client_name",
    "fd_a_client_type",
    "fd_a_lead_source",
    "fd_a_email_address",
    "fd_a_urgency_flag",
    "fd_a_case_type",
    "fd_a_case_worker_assigned",
    "fd_a_call_outcome",
    "enquiry_details",
    "fd_a_call_summary",
]

# fd_a_client_type may only ever hold one of the Calls-page dropdown values
# (mirrors FlintDesk/src/data/callFieldOptions.js CLIENT_TYPE_OPTIONS).
CLIENT_TYPE_VALUES = (
    "New Client - Individual",
    "New Client - Organization",
    "Existing client - Individual",
    "Existing client - Organization",
    "Voicemail Call",
    "Spam / Wrong Call",
)
_CLIENT_TYPE_BY_LOWER = {value.lower(): value for value in CLIENT_TYPE_VALUES}


def normalize_client_type(value):
    """Map an LLM value onto the dropdown values, or None when it is not one."""
    return _CLIENT_TYPE_BY_LOWER.get(str(value or "").strip().lower())


# NOTE: for records Vitel reports as disposition=VOICEMAIL, F3 overrides this
# model's voicemail verdict with the metadata (vitel_metadata.apply_metadata_voicemail).
FLINTDESK_ANALYSIS_PROMPT = """
You are extracting structured findings from a legal call transcript for FlintDesk.
Return only valid JSON with exactly these keys and no extra keys:
{
  "fd_a_client_name": "...",
  "fd_a_client_type": "...",
  "fd_a_lead_source": "...",
  "fd_a_email_address": "...",
  "fd_a_urgency_flag": "...",
  "fd_a_case_type": "...",
  "fd_a_case_worker_assigned": "...",
  "fd_a_call_outcome": "...",
  "enquiry_details": "...",
  "fd_a_call_summary": "..."
}

Rules:
- Use only information present in the transcript or metadata.
- If the value cannot be determined, return null.
- fd_a_client_type must be EXACTLY one of these values, or null if the transcript does not support any:
  "New Client - Individual", "New Client - Organization", "Existing client - Individual",
  "Existing client - Organization", "Voicemail Call", "Spam / Wrong Call".
  New = a first-time or prospective client enquiring; Existing = the caller is already a client or has an
  ongoing case with the firm. Individual = a person calling about their own matter; Organization = a company,
  employer or law office calling on behalf of an organisation. Voicemail Call = only a voicemail greeting or
  message. Spam / Wrong Call = sales/robocalls or a wrong number. Never return any other wording.
- Do not hallucinate. Do not invent names, email addresses, case workers, dates, or outcomes.
- Follow SUMMARY LENGTH GUIDANCE below. Calls strictly over 180 seconds require a detailed summary.
- Extract the actual client from the transcript, distinguishing the caller from staff introductions.
  Gokul, Satish, Shrishti, Priyanka, Mary Kennedy (also Mary Kennady) are internal participants,
  never client names. Do not guess if only an internal participant is named; return null.
  A distinct client with a full name sharing a staff first name is allowed when clearly identified as the client.
- Use values like "Yes", "No", or "Unknown" only if clearly supported by the transcript; otherwise use null.
- enquiry_details is the client's own enquiry/question in their own words -- what they actually
  called about or asked (e.g. "Status of pending I-485 application", "Can I switch from F1 to H1B").
  Keep it to one short sentence. This is distinct from fd_a_call_summary, which covers the whole call.
"""


class FDLLM:
    """FlintDesk LLM node dedicated to fd_a_* extraction only."""

    def __init__(self):
        if not LLM_API_KEY:
            raise ValueError("LLM_API_KEY not found")
        if not LLM_URL:
            raise ValueError("LLM_URL not found")

        self.api_key = LLM_API_KEY
        self.url = LLM_URL
        self.model = LLM_MODEL

    def _build_metadata_block(self, call_record) -> str:
        metadata = {
            "call_id": str(getattr(call_record, "call_id", "")),
            "src": getattr(call_record, "src", None),
            "dst": getattr(call_record, "dst", None),
            "billsec": getattr(call_record, "billsec", None),
            "disposition": getattr(call_record, "disposition", None),
            "extension": getattr(call_record, "extension", None),
            "extname": getattr(call_record, "extname", None),
            "call_date": getattr(call_record, "call_date", None),
            "call_time": getattr(call_record, "call_time", None),
            "audio_s3_uri": getattr(call_record, "audio_s3_uri", None),
            "processing_status": getattr(call_record, "processing_status", None),
        }
        return json.dumps(metadata, default=str, indent=2)

    @staticmethod
    def summary_length_guidance(call_record, audio_duration_seconds=None) -> str:
        seconds = float(audio_duration_seconds if audio_duration_seconds is not None else getattr(call_record, "billsec", 0) or 0)
        if seconds > 180:
            return ("LONG CALL: provide a very detailed, multi-paragraph summary. Cover the call purpose, "
                    "conversation flow, important facts, client requirements and questions, agent responses, "
                    "concerns and objections, actions taken, commitments, next steps, and final outcome. "
                    "Include all materially relevant details supported by the transcript; do not invent missing facts.")
        if seconds < 120:
            return "SHORT CALL: summarize in 1-2 sentences."
        return "MEDIUM CALL: summarize in one concise paragraph."

    @staticmethod
    def validate_client_name(value, call_record):
        if not isinstance(value, str):
            return None
        normalize = lambda name: re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", name.casefold())).strip()
        excluded = {"gokul", "satish", "shrishti", "priyanka", "mary kennedy", "mary kennady"}
        agent = getattr(call_record, "extname", None)
        if isinstance(agent, str) and agent.strip():
            excluded.add(normalize(agent))
        return None if normalize(value) in excluded else value.strip() or None

    @staticmethod
    def audio_duration(call_record):
        reference = getattr(call_record, "audio_s3_uri", None) or getattr(call_record, "audio_s3_key", None)
        if not reference:
            logging.getLogger(__name__).warning("Audio duration unavailable for call %s; using billsec", getattr(call_record, "call_id", None))
            return None
        from services.Database_sync.f1 import F1Processor
        probe = F1Processor()
        bucket, key = probe.s3._parse_s3_uri(reference)
        probe.bucket = getattr(call_record, "audio_s3_bucket", None) or bucket
        return probe._get_wav_duration_seconds(key)

    def build_context(self, transcript: str, call_record, stage2_prompts=None) -> str:
        audio_duration_seconds = self.audio_duration(call_record)
        metadata_block = self._build_metadata_block(call_record)
        context = self._stage1_context(transcript, call_record, audio_duration_seconds, metadata_block)
        if stage2_prompts:
            from services import stage2_automation as s2
            entry = s2.entry_datetime(call_record)
            if entry is not None:
                context += "\n\n" + s2.build_stage2_block(stage2_prompts, call_record, entry)
        return context

    def _stage1_context(self, transcript, call_record, audio_duration_seconds, metadata_block) -> str:
        return f"""
{FLINTDESK_ANALYSIS_PROMPT}

CALL METADATA FROM DATABASE:
{metadata_block}

SUMMARY LENGTH GUIDANCE:
{self.summary_length_guidance(call_record, audio_duration_seconds)}
Actual audio duration (seconds): {audio_duration_seconds if audio_duration_seconds is not None else 'unavailable; billsec fallback'}

CALL TRANSCRIPT:
{transcript or ''}
""".strip()

    def _post_with_retry(self, payload):
        """POST to OpenAI, retrying 429s with exponential backoff + jitter.

        Honors the Retry-After header when present, logs the remaining
        request/token quota so real limits are visible in logs, and raises
        LLMRateLimitExhausted after F3_LLM_MAX_RETRIES so the caller can mark
        the call as failed instead of silently dropping it.
        """
        last_exc = None
        for attempt in range(F3_LLM_MAX_RETRIES + 1):
            with _rate_limit_hits_lock:
                unavailable_until = _provider_circuit["unavailable_until"]
            if unavailable_until > time.monotonic():
                raise LLMQuotaExhausted("OpenAI credits are unavailable; retry deferred by provider circuit breaker")
            # Shared token bucket caps total requests/min across all F3 threads.
            _RATE_LIMITER.acquire()

            response = requests.post(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )

            remaining_requests = response.headers.get("x-ratelimit-remaining-requests")
            remaining_tokens = response.headers.get("x-ratelimit-remaining-tokens")
            if remaining_requests is not None or remaining_tokens is not None:
                logger.info(
                    "OpenAI rate-limit headers: remaining-requests=%s remaining-tokens=%s",
                    remaining_requests, remaining_tokens,
                )

            if response.status_code != 429:
                response.raise_for_status()
                record_llm_response(response)  # usage ledger only; never raises
                # No 429 this call: nudge capacity back toward the configured
                # limit so an earlier throttle-down doesn't stick forever.
                _RATE_LIMITER.recover_up()
                return response

            try:
                provider_error = response.json().get("error", {})
            except (TypeError, ValueError):
                provider_error = {}
            if provider_error.get("code") in {"insufficient_quota", "billing_hard_limit_reached"}:
                with _rate_limit_hits_lock:
                    _provider_circuit["unavailable_until"] = time.monotonic() + _PROVIDER_RECHECK_SECONDS
                raise LLMQuotaExhausted(
                    "OpenAI credit balance is exhausted; add credits before retrying pending analyses"
                )

            with _rate_limit_hits_lock:
                _rate_limit_hits["count"] += 1
            new_capacity = _RATE_LIMITER.throttle_down()
            logger.warning("OpenAI 429 observed; shrinking shared rate limit to %.1f rpm", new_capacity)

            if attempt == F3_LLM_MAX_RETRIES:
                raise LLMRateLimitExhausted(
                    f"OpenAI 429 after {F3_LLM_MAX_RETRIES} retries: {response.text[:300]}"
                )

            retry_after = response.headers.get("retry-after")
            if retry_after is not None:
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = F3_LLM_BACKOFF_BASE_SECONDS
            else:
                delay = min(F3_LLM_BACKOFF_MAX_SECONDS, F3_LLM_BACKOFF_BASE_SECONDS * (2 ** attempt))
            delay += random.uniform(0, delay * 0.25)  # jitter to avoid thundering herd

            logger.warning(
                "OpenAI 429 (attempt %s/%s), backing off %.1fs",
                attempt + 1, F3_LLM_MAX_RETRIES, delay,
            )
            time.sleep(delay)
            last_exc = None

        raise last_exc or LLMRateLimitExhausted("OpenAI 429 retries exhausted")

    def request_analysis(self, transcript: str, call_record, stage2_prompts=None) -> dict:
        """Call the shared LLM API to extract FlintDesk fd_a_* values.

        With stage2_prompts (fd_prompts) the same single request also returns the
        Stage 2 action plan under a "stage2" key; without them it is Stage 1 only.
        """
        user_prompt = self.build_context(transcript, call_record, stage2_prompts)

        response = self._post_with_retry({
            "model": self.model,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        })

        return response.json()

    def generate_stage2(self, transcript: str, call_record, prompts: dict, attempts: int = 2):
        """Generate the validated Stage 2 values (action item, due date, follow-up).

        Prompts come from fd_prompts; every returned value is validated by the
        backend (services.stage2_automation) regardless of what the model says.
        Returns None when no usable result was produced; never fabricates one.
        """
        from services import stage2_automation as s2

        if not (transcript or "").strip():
            return None
        entry = s2.entry_datetime(call_record)
        if entry is None:
            logger.warning("stage2: call %s has no entry time; skipped", getattr(call_record, "call_id", None))
            return None
        messages = s2.build_messages(prompts, transcript, call_record, entry)
        for attempt in range(attempts):
            response = self._post_with_retry({
                "model": self.model, "messages": messages, "temperature": 0,
                "response_format": {"type": "json_object"},
            })
            try:
                content = response.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError, ValueError):
                content = None
            result = s2.resolve_result(s2.parse_model_json(content), entry)
            if result is not None:
                result["prompt_versions"] = {
                    f"{key}_version": prompt["version"] for key, prompt in prompts.items()
                }
                return result
            logger.warning("stage2: unusable model output for call %s (attempt %s/%s)",
                           getattr(call_record, "call_id", None), attempt + 1, attempts)
        return None

    def generate_flintdesk_analysis(self, transcript: str, call_record) -> dict:
        return self.parse_response(self.request_analysis(transcript, call_record), call_record)

    def parse_response(self, response, call_record) -> dict:
        content = response["choices"][0]["message"]["content"].strip()
        cleaned = content.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise ValueError("LLM response was not a JSON object.")

        normalized = {}
        for key, value in parsed.items():
            if value is None:
                normalized[key] = None
            elif isinstance(value, str):
                cleaned_value = value.strip()
                normalized[key] = cleaned_value or None
            else:
                normalized[key] = value

        # Keep the FlintDesk AI output isolated from the AutoFlint contract.
        for key in FD_AI_FIELDS:
            if key not in normalized:
                normalized[key] = None

        normalized["fd_a_client_type"] = normalize_client_type(normalized.get("fd_a_client_type"))
        normalized["fd_a_client_name"] = self.validate_client_name(normalized.get("fd_a_client_name"), call_record)
        return normalized
