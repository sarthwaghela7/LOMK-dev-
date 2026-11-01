"""Cost calculations. No prices live in code: every rate comes from settings and
an unset rate yields UNAVAILABLE, never a guess."""
import calendar

from .models import ACTUAL, ESTIMATED, UNAVAILABLE, Metric, Window, now_utc, unavailable

COST_KEYS = ("stt_cost", "llm_cost", "railway_cost", "supabase_cost", "s3_request_cost", "s3_storage_cost")
LABELS = {"stt_cost": "STT", "llm_cost": "LLM", "railway_cost": "Railway", "supabase_cost": "Supabase",
          "s3_request_cost": "S3 requests/transfer", "s3_storage_cost": "S3 storage"}


def stt_cost(minutes: Metric, settings, window: Window) -> Metric:
    src = "audio minutes x MONITOR_STT_PRICE_PER_HOUR"
    if settings.stt_price_per_hour is None:
        return unavailable(window, src, "MONITOR_STT_PRICE_PER_HOUR not configured")
    if minutes.value is None:
        return unavailable(window, src, "audio minutes unavailable")
    return Metric(round(minutes.value / 60 * settings.stt_price_per_hour, 6), ESTIMATED, src, now_utc(),
                  window.start, window.end,
                  "estimate; ElevenLabs bills against plan credits/overage so the invoice can differ")


def llm_cost(provider_cost: Metric | None, tokens_in: Metric, tokens_out: Metric, tokens_cached: Metric,
             settings, window: Window) -> Metric:
    """Prefer the provider-reported billed cost; otherwise tokens x configured rates."""
    if provider_cost is not None and provider_cost.kind == ACTUAL:
        return provider_cost
    src = "response usage tokens x MONITOR_LLM_PRICE_*"
    if settings.llm_price_input_per_1m is None or settings.llm_price_output_per_1m is None:
        return unavailable(window, src, "OpenAI cost API unavailable and MONITOR_LLM_PRICE_INPUT_PER_1M / _OUTPUT_PER_1M not configured")
    if tokens_in.value is None or tokens_out.value is None:
        return unavailable(window, src, "token usage unavailable")
    cached = tokens_cached.value or 0
    if settings.llm_price_cached_input_per_1m is not None:
        fresh_cost = (tokens_in.value - cached) / 1e6 * settings.llm_price_input_per_1m
        cached_cost = cached / 1e6 * settings.llm_price_cached_input_per_1m
        note = "estimate = uncached input + cached input + output at configured rates"
    else:
        fresh_cost, cached_cost = tokens_in.value / 1e6 * settings.llm_price_input_per_1m, 0.0
        note = "estimate; cached-input discount not configured so all input is priced at the full rate (upper bound)"
    total = fresh_cost + cached_cost + tokens_out.value / 1e6 * settings.llm_price_output_per_1m
    return Metric(round(total, 6), ESTIMATED, src, now_utc(), window.start, window.end, note)


def supabase_cost(from_snapshots: Metric, settings, window: Window) -> Metric:
    if from_snapshots.kind == ACTUAL:
        return from_snapshots
    if settings.supabase_fixed_monthly_usd is None:
        return from_snapshots  # already unavailable with its own reason
    days = calendar.monthrange(window.day.year, window.day.month)[1]
    return Metric(round(settings.supabase_fixed_monthly_usd / days, 6), ESTIMATED,
                  "MONITOR_SUPABASE_FIXED_MONTHLY_USD / days in month", now_utc(), window.start, window.end,
                  "plan fee prorated; excludes usage overages")


def total_and_type(costs: dict) -> tuple[float | None, str, list]:
    """Sum only components that have a value. Returns (total, billing_type, missing).

    billing_type uses the tracker's dropdown: 'Actual' only when every component
    is present and actual; any estimate or any gap => 'Estimated'; nothing => 'Not Available'.
    A gap never becomes 0 in a component, but the total is then partial; callers
    must surface ``missing``."""
    present = {k: m for k, m in costs.items() if m.value is not None}
    missing = [LABELS[k] for k in COST_KEYS if k not in present]
    if not present:
        return None, "Not Available", missing
    total = round(sum(m.value for m in present.values()), 6)
    all_actual = not missing and all(m.kind == ACTUAL for m in present.values())
    return total, ("Actual" if all_actual else "Estimated"), missing


def cost_per_stt_call(total, calls_stt_completed):
    """total daily cost / calls STT completed (same denominator as the workbook formula)."""
    if total is None or not calls_stt_completed:
        return None
    return round(total / calls_stt_completed, 6)
