"""Environment-driven settings. Only variable NAMES are documented; values are
never logged. Nothing here is a hard-coded price: unset price => no estimate."""
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


def _float(name):
    raw = os.getenv(name)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _bool(name, default=False):
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Day boundaries. UTC by default because every provider bills in UTC days.
    report_tz: str = "UTC"
    # Configurable verified prices (USD). None => cost is not estimated.
    llm_price_input_per_1m: float | None = None
    llm_price_cached_input_per_1m: float | None = None
    llm_price_output_per_1m: float | None = None
    stt_price_per_hour: float | None = None
    supabase_fixed_monthly_usd: float | None = None
    # Quality thresholds.
    spike_ratio: float = 2.0
    spike_min_usd: float = 1.0
    failure_min_count: int = 3
    failure_ratio: float = 2.0
    # Provider switches / identifiers.
    openai_admin_key: str | None = None
    openai_project_id: str | None = None
    elevenlabs_api_key: str | None = None
    elevenlabs_usage_enabled: bool = False
    railway_token: str | None = None
    railway_project_id: str | None = None
    railway_environment_id: str | None = None
    s3_bucket: str | None = None
    aws_region: str | None = None
    aws_cost_explorer_enabled: bool = False
    aws_cost_tag_key: str | None = None
    aws_cost_tag_value: str | None = None
    aws_include_adjustments: bool = False

    @property
    def tz(self):
        return ZoneInfo(self.report_tz)


def load_settings() -> Settings:
    return Settings(
        report_tz=os.getenv("MONITOR_REPORT_TZ", "UTC"),
        llm_price_input_per_1m=_float("MONITOR_LLM_PRICE_INPUT_PER_1M"),
        llm_price_cached_input_per_1m=_float("MONITOR_LLM_PRICE_CACHED_INPUT_PER_1M"),
        llm_price_output_per_1m=_float("MONITOR_LLM_PRICE_OUTPUT_PER_1M"),
        stt_price_per_hour=_float("MONITOR_STT_PRICE_PER_HOUR"),
        supabase_fixed_monthly_usd=_float("MONITOR_SUPABASE_FIXED_MONTHLY_USD"),
        spike_ratio=_float("MONITOR_SPIKE_RATIO") or 2.0,
        spike_min_usd=_float("MONITOR_SPIKE_MIN_USD") or 1.0,
        failure_min_count=int(_float("MONITOR_FAILURE_MIN_COUNT") or 3),
        failure_ratio=_float("MONITOR_FAILURE_RATIO") or 2.0,
        openai_admin_key=os.getenv("OPENAI_ADMIN_KEY") or None,
        openai_project_id=os.getenv("OPENAI_PROJECT_ID") or None,
        elevenlabs_api_key=os.getenv("ELEVENLABS_USAGE_API_KEY") or os.getenv("STT_API_KEY") or None,
        elevenlabs_usage_enabled=_bool("MONITOR_ELEVENLABS_USAGE_ENABLED"),
        railway_token=os.getenv("RAILWAY_API_TOKEN") or None,
        railway_project_id=os.getenv("RAILWAY_PROJECT_ID") or None,
        railway_environment_id=os.getenv("RAILWAY_ENVIRONMENT_ID") or None,
        s3_bucket=os.getenv("AWS_S3_BUCKET_NAME") or None,
        aws_region=os.getenv("AWS_REGION") or None,
        aws_cost_explorer_enabled=_bool("MONITOR_AWS_COST_EXPLORER_ENABLED"),
        aws_cost_tag_key=os.getenv("MONITOR_AWS_COST_TAG_KEY") or None,
        aws_cost_tag_value=os.getenv("MONITOR_AWS_COST_TAG_VALUE") or None,
        aws_include_adjustments=_bool("MONITOR_AWS_INCLUDE_ADJUSTMENTS"),
    )
