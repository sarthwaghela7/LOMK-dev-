"""OpenAI organization Usage + Costs APIs (requires an Admin API key).

Endpoints (OpenAI docs): GET /v1/organization/costs and
GET /v1/organization/usage/completions with ``bucket_width=1d``. Buckets are UTC
days, so a non-UTC reporting window is refused rather than approximated.
Parameter names should be confirmed against the current API reference with
``python -m monitoring --check-providers`` before relying on the numbers.
"""
from ..models import ACTUAL, MEASURED, Metric, ProviderResult, Window, now_utc, unavailable

BASE = "https://api.openai.com/v1/organization"
SRC_COST = "OpenAI /v1/organization/costs"
SRC_USAGE = "OpenAI /v1/organization/usage/completions"


def _get(http, key, path, params):
    response = http.get(f"{BASE}/{path}", params=params, timeout=30,
                        headers={"Authorization": f"Bearer {key}"})
    if response.status_code != 200:
        # Status only; the body can echo organisation details.
        raise RuntimeError(f"HTTP {response.status_code}")
    return response.json()


def fetch(settings, window: Window, *, http) -> ProviderResult:
    result = ProviderResult()
    at = now_utc()
    if not settings.openai_admin_key:
        reason = "OPENAI_ADMIN_KEY not configured"
        result.metrics["llm_cost"] = unavailable(window, SRC_COST, reason, at)
        result.warnings.append(f"OpenAI cost: {reason}")
        return result
    if not window.is_utc_aligned:
        reason = f"OpenAI bills in UTC days; report timezone {window.tz_name} cannot be matched"
        result.metrics["llm_cost"] = unavailable(window, SRC_COST, reason, at)
        result.warnings.append(f"OpenAI cost: {reason}")
        return result
    start, end = int(window.start.timestamp()), int(window.end.timestamp())
    params = {"start_time": start, "end_time": end, "bucket_width": "1d", "limit": 2}
    if settings.openai_project_id:
        params["project_ids"] = [settings.openai_project_id]
    try:
        body = _get(http, settings.openai_admin_key, "costs", params)
        buckets = [b for b in body.get("data", []) if b.get("results")]
        if not buckets:
            reason = "no cost bucket returned for this day (provider billing data can lag up to a day)"
            result.metrics["llm_cost"] = unavailable(window, SRC_COST, reason, at)
            result.warnings.append(f"OpenAI cost: {reason}")
        else:
            currencies = {r["amount"].get("currency", "usd").lower()
                          for b in buckets for r in b["results"]}
            if currencies - {"usd"}:
                reason = f"non-USD currency returned: {sorted(currencies)}"
                result.metrics["llm_cost"] = unavailable(window, SRC_COST, reason, at)
                result.warnings.append(f"OpenAI cost: {reason}")
            else:
                total = sum(float(r["amount"]["value"]) for b in buckets for r in b["results"])
                scope = "project " + settings.openai_project_id if settings.openai_project_id else \
                    "whole organisation (set OPENAI_PROJECT_ID to scope to this app)"
                result.metrics["llm_cost"] = Metric(round(total, 6), ACTUAL, SRC_COST, at, window.start, window.end,
                                                    f"scope: {scope}; may be revised by later billing adjustments")
    except Exception as exc:  # noqa: BLE001
        reason = f"request failed: {type(exc).__name__}: {str(exc)[:100]}"
        result.metrics["llm_cost"] = unavailable(window, SRC_COST, reason, at)
        result.warnings.append(f"OpenAI cost: {reason}")

    # Provider-side token totals, used only to reconcile with the app ledger.
    try:
        usage_params = {"start_time": start, "end_time": end, "bucket_width": "1d", "limit": 2}
        if settings.openai_project_id:
            usage_params["project_ids"] = [settings.openai_project_id]
        body = _get(http, settings.openai_admin_key, "usage/completions", usage_params)
        rows = [r for b in body.get("data", []) for r in b.get("results", [])]
        if rows:
            result.metrics["provider_llm_input_tokens"] = Metric(
                sum(int(r.get("input_tokens", 0)) for r in rows), MEASURED, SRC_USAGE, at, window.start, window.end)
            result.metrics["provider_llm_output_tokens"] = Metric(
                sum(int(r.get("output_tokens", 0)) for r in rows), MEASURED, SRC_USAGE, at, window.start, window.end)
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"OpenAI usage reconciliation unavailable: {type(exc).__name__}")
    return result
