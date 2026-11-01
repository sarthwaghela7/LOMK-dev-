"""Zero-manual-input OpenAI rate-limit detection.

Priority order (highest first):
    1. Explicit OPENAI_RPM_LIMIT / OPENAI_TPM_LIMIT env vars (existing config
       set by ops/deployment — e.g. Render environment variables).
    2. A cached result from an explicitly enabled diagnostic probe.
    3. Conservative safe defaults.

Nothing here ever prompts for input or requires a manual dashboard lookup.
"""
import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

SAFE_DEFAULT_RPM = 60
SAFE_DEFAULT_TPM = 40000
_CACHE_TTL_SECONDS = 24 * 60 * 60  # re-probe at most once a day


def _cache_path(base_dir):
    return base_dir / ".openai_limits_cache.json"


def _read_cache(base_dir):
    path = _cache_path(base_dir)
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("checked_at", 0) < _CACHE_TTL_SECONDS:
            return data
    except (OSError, ValueError):
        pass
    return None


def _write_cache(base_dir, rpm, tpm):
    try:
        _cache_path(base_dir).write_text(json.dumps({
            "rpm": rpm, "tpm": tpm, "checked_at": time.time(),
        }))
    except OSError:
        pass  # Cache is a convenience; never fail startup over it.


def _probe_openai(api_key, url, model):
    """Make one minimal chat/completions call and read the rate-limit headers."""
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
        timeout=15,
    )
    rpm = response.headers.get("x-ratelimit-limit-requests")
    tpm = response.headers.get("x-ratelimit-limit-tokens")
    if rpm is None and tpm is None:
        raise ValueError("OpenAI response did not include rate-limit headers")
    return (int(rpm) if rpm else None, int(tpm) if tpm else None)


def detect_openai_limits(base_dir, api_key, url, model):
    """Return (rpm, tpm, source) using env override > cached/live probe > safe default."""
    env_rpm = os.getenv("OPENAI_RPM_LIMIT")
    env_tpm = os.getenv("OPENAI_TPM_LIMIT")
    if env_rpm or env_tpm:
        return (
            int(env_rpm) if env_rpm else SAFE_DEFAULT_RPM,
            int(env_tpm) if env_tpm else SAFE_DEFAULT_TPM,
            "env",
        )

    cached = _read_cache(base_dir)
    if cached:
        return cached["rpm"], cached["tpm"], "cache"

    # Never spend provider quota or add network latency during ordinary module
    # import/application startup. Operators may opt into the diagnostic probe.
    probe_enabled = os.getenv("OPENAI_LIMIT_PROBE_ENABLED", "false").lower() == "true"
    if probe_enabled and api_key and "api.openai.com" in (url or ""):
        try:
            rpm, tpm = _probe_openai(api_key, url, model)
            rpm = rpm or SAFE_DEFAULT_RPM
            tpm = tpm or SAFE_DEFAULT_TPM
            _write_cache(base_dir, rpm, tpm)
            logger.info("Detected live OpenAI rate limits: rpm=%s tpm=%s", rpm, tpm)
            return rpm, tpm, "live-probe"
        except Exception as exc:
            logger.warning(
                "OpenAI rate-limit probe failed (%s); using safe defaults rpm=%s tpm=%s",
                exc, SAFE_DEFAULT_RPM, SAFE_DEFAULT_TPM,
            )

    return SAFE_DEFAULT_RPM, SAFE_DEFAULT_TPM, "default"
