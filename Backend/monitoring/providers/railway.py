"""Railway public GraphQL API (https://backboard.railway.com/graphql/v2).

What is and is not verified:
  * The endpoint and bearer-token auth are documented.
  * Railway documents ``usage`` / ``estimatedUsage`` queries but no per-day
    billed-cost field. The queries below are therefore best-effort and any
    GraphQL error becomes an explicit warning with the metric left unavailable.
  * The metric column reports real resource consumption from ``usage``; it is
    never replaced by request counts.
  * Cost is NOT derived here. It comes from month-to-date snapshots
    (cumulative.py), either entered from the dashboard or taken from
    ``estimatedUsage`` (labelled estimated), and is converted to a daily delta.
"""
from ..models import ESTIMATED, MEASURED, Metric, ProviderResult, Window, now_utc, unavailable

URL = "https://backboard.railway.com/graphql/v2"
MEASUREMENTS = ["CPU_USAGE", "MEMORY_USAGE_GB", "NETWORK_TX_GB", "DISK_USAGE_GB"]
SRC = "Railway GraphQL usage"

USAGE_QUERY = """
query Usage($projectId: String!, $start: DateTime!, $end: DateTime!, $m: [MetricMeasurement!]!) {
  usage(projectId: $projectId, measurements: $m, startDate: $start, endDate: $end) { measurement value }
}"""
ESTIMATE_QUERY = """
query Estimate($projectId: String!, $m: [MetricMeasurement!]!) {
  estimatedUsage(projectId: $projectId, measurements: $m) { measurement estimatedValue }
}"""


def _post(http, token, query, variables):
    response = http.post(URL, json={"query": query, "variables": variables}, timeout=30,
                         headers={"Authorization": f"Bearer {token}"})
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    body = response.json()
    if body.get("errors"):
        raise RuntimeError("GraphQL: " + str(body["errors"][0].get("message", "error"))[:150])
    return body["data"]


def fetch_usage_metric(settings, window: Window, *, http) -> ProviderResult:
    result, at = ProviderResult(), now_utc()
    if not (settings.railway_token and settings.railway_project_id):
        reason = "RAILWAY_API_TOKEN / RAILWAY_PROJECT_ID not configured"
        result.metrics["railway_usage"] = unavailable(window, SRC, reason, at)
        result.warnings.append(f"Railway usage: {reason}")
        return result
    try:
        data = _post(http, settings.railway_token, USAGE_QUERY, {
            "projectId": settings.railway_project_id, "start": window.start.isoformat(),
            "end": window.end.isoformat(), "m": MEASUREMENTS})
        parts = [f"{row['measurement']}={row['value']}" for row in data.get("usage", [])]
        if not parts:
            raise RuntimeError("empty usage result")
        result.metrics["railway_usage"] = Metric("; ".join(parts), MEASURED, SRC, at, window.start, window.end,
                                                 "raw Railway measurement units; not request counts")
    except Exception as exc:  # noqa: BLE001
        reason = f"{type(exc).__name__}: {str(exc)[:150]}"
        result.metrics["railway_usage"] = unavailable(window, SRC, reason, at)
        result.warnings.append(f"Railway usage unavailable: {reason}")
    return result


def fetch_mtd_estimate(settings, *, http):
    """Cumulative current-period estimate in raw measurement units, or (None, why).
    Railway returns usage quantities, not dollars, so this is NOT used as a cost
    unless the operator supplies rates; kept for diagnostics (--check-providers)."""
    if not (settings.railway_token and settings.railway_project_id):
        return None, "not configured"
    try:
        data = _post(http, settings.railway_token, ESTIMATE_QUERY,
                     {"projectId": settings.railway_project_id, "m": MEASUREMENTS})
        return data.get("estimatedUsage"), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {str(exc)[:150]}"
