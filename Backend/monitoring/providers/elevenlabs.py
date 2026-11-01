"""ElevenLabs usage, used ONLY to reconcile our own submitted-minutes figure.

Cost is not read from ElevenLabs: its usage API documents a ``fiat_units_spent``
metric but its unit and STT coverage are unverified, and ElevenLabs bills STT
against plan credits. Cost is estimated in costing.py from measured minutes x a
configured price. The endpoint used is the documented (now legacy)
``/v1/usage/character-stats``; it needs an API key with usage permission.
"""
from ..models import MEASURED, Metric, ProviderResult, Window, now_utc

URL = "https://api.elevenlabs.io/v1/usage/character-stats"
SRC = "ElevenLabs /v1/usage/character-stats (metric=minutes_used)"


def fetch(settings, window: Window, *, http) -> ProviderResult:
    result = ProviderResult()
    if not settings.elevenlabs_usage_enabled:
        return result  # opt-in; silence is not an error
    at = now_utc()
    if not settings.elevenlabs_api_key:
        result.warnings.append("ElevenLabs usage reconciliation: no API key configured")
        return result
    try:
        response = http.get(URL, timeout=30, headers={"xi-api-key": settings.elevenlabs_api_key}, params={
            "start_unix": int(window.start.timestamp() * 1000),
            "end_unix": int(window.end.timestamp() * 1000),
            "aggregation_interval": "day", "metric": "minutes_used"})
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        usage = response.json().get("usage") or {}
        series = next(iter(usage.values()), None)
        if series:
            result.metrics["provider_stt_minutes"] = Metric(
                round(float(sum(series)), 4), MEASURED, SRC, at, window.start, window.end,
                "provider-side minutes; STT coverage of this metric is unverified")
        else:
            result.warnings.append("ElevenLabs usage reconciliation: empty usage series")
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"ElevenLabs usage reconciliation failed: {type(exc).__name__}: {str(exc)[:100]}")
    return result
