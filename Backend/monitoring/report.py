"""Builds and persists one daily report. Orchestration only: aggregation,
provider calls, costing and quality rules live in their own modules."""
import json
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql, sqlite

from . import aggregation, costing, cumulative, quality
from .models import ACTUAL, ESTIMATED, MEASURED, UNAVAILABLE, Metric, Window, now_utc, unavailable
from .providers import aws_s3, db_size, elevenlabs, openai_usage, railway, supabase
from .schema import usage_daily_reports

COLUMNS = [
    "Date", "Calls Ingested", "Calls STT Completed", "Audio Minutes Sent to STT", "STT Cost (USD)",
    "LLM Analyses Completed", "LLM Input Tokens", "LLM Output Tokens", "LLM Cost (USD)",
    "Railway Cost (USD)", "Railway Usage / Metric", "Supabase Cost (USD)", "Supabase DB Size (GB)",
    "Supabase Egress (GB)", "S3 Storage (GB)", "S3 Requests / Transfer Cost (USD)", "S3 Storage Cost (USD)",
    "Total Daily Cost (USD)", "Cost per STT-Completed Call (USD)", "STT Failures", "LLM Failures",
    "Billing Data Type", "Notes / Issues",
]
# metric key -> workbook column
KEY_TO_COLUMN = {
    "calls_ingested": "Calls Ingested", "calls_stt_completed": "Calls STT Completed",
    "audio_minutes": "Audio Minutes Sent to STT", "stt_cost": "STT Cost (USD)",
    "llm_completed": "LLM Analyses Completed", "llm_input_tokens": "LLM Input Tokens",
    "llm_output_tokens": "LLM Output Tokens", "llm_cost": "LLM Cost (USD)",
    "railway_cost": "Railway Cost (USD)", "railway_usage": "Railway Usage / Metric",
    "supabase_cost": "Supabase Cost (USD)", "supabase_db_size": "Supabase DB Size (GB)",
    "supabase_egress": "Supabase Egress (GB)", "s3_storage_gb": "S3 Storage (GB)",
    "s3_request_cost": "S3 Requests / Transfer Cost (USD)", "s3_storage_cost": "S3 Storage Cost (USD)",
    "stt_failures": "STT Failures", "llm_failures": "LLM Failures",
}
# Provider-sourced keys that may be carried over from an earlier revision when
# today's attempt failed (so a transient API error cannot erase a good figure).
CARRY_KEYS = {"stt_cost", "llm_cost", "railway_cost", "railway_usage", "supabase_cost", "supabase_egress",
              "s3_storage_gb", "s3_request_cost", "s3_storage_cost"}


@dataclass
class DailyReport:
    day: date
    tz_name: str
    metrics: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    billing_type: str = "Not Available"
    revision: int = 1


def build_report(day: date, settings, *, app_conn, store_conn, http, cloudwatch=None, cost_explorer=None,
                 cycle_days=None, today: date | None = None) -> DailyReport:
    window = Window(day, settings.report_tz)
    cycle_days = cycle_days or {}
    warnings, m = [], {}

    m.update(aggregation.collect_app_metrics(app_conn, window))
    m["supabase_db_size"] = db_size.database_size_gb(app_conn, window)
    if m["supabase_db_size"].value is None:
        warnings.append("Supabase DB size: " + (m["supabase_db_size"].note or "unavailable"))

    for result in (
        openai_usage.fetch(settings, window, http=http),
        elevenlabs.fetch(settings, window, http=http),
        railway.fetch_usage_metric(settings, window, http=http),
        supabase.fetch(settings, window),
        aws_s3.fetch(settings, window, cloudwatch=cloudwatch, cost_explorer=cost_explorer)
        if cloudwatch is not None else _aws_skipped(window),
    ):
        m.update(result.metrics)
        warnings.extend(result.warnings)

    # Costs ---------------------------------------------------------------
    m["stt_cost"] = costing.stt_cost(m["audio_minutes"], settings, window)
    m["llm_cost"] = costing.llm_cost(m.get("llm_cost"), m["llm_input_tokens"], m["llm_output_tokens"],
                                     m["llm_cached_input_tokens"], settings, window)
    for provider, key in (("railway", "railway_cost"), ("supabase", "supabase_cost")):
        metric, warn = cumulative.daily_from_cumulative(store_conn, provider, "cost", window,
                                                        cycle_days.get(provider, 1))
        m[key] = metric
        if warn and provider != "supabase":
            warnings.append(warn)
    m["supabase_cost"] = costing.supabase_cost(m["supabase_cost"], settings, window)
    if m["supabase_cost"].value is None:
        warnings.append("Supabase cost: " + (m["supabase_cost"].note or "unavailable"))
    for key in ("stt_cost", "llm_cost"):
        if m[key].value is None:
            warnings.append(f"{costing.LABELS[key]} cost unavailable: {m[key].note}")

    # Reconcile app measurements with provider-side totals ------------------
    for label, ours, theirs in (
        ("LLM input tokens", m["llm_input_tokens"].value, _val(m, "provider_llm_input_tokens")),
        ("LLM output tokens", m["llm_output_tokens"].value, _val(m, "provider_llm_output_tokens")),
        ("STT minutes", m["audio_minutes"].value, _val(m, "provider_stt_minutes")),
    ):
        w = quality.reconcile(label, ours, theirs)
        if w:
            warnings.append(w)

    report = DailyReport(day, settings.report_tz, m, warnings=warnings)
    _carry_forward(report, store_conn)
    _finalise(report, store_conn, settings, window, today)
    return report


def _val(m, key):
    return m[key].value if key in m else None


def _aws_skipped(window):
    from .models import ProviderResult
    r = ProviderResult()
    for k in ("s3_storage_gb", "s3_storage_cost", "s3_request_cost"):
        r.metrics[k] = unavailable(window, "AWS", "AWS clients not available (boto3 session not created)")
    r.warnings.append("AWS S3: clients unavailable")
    return r


def _previous(store_conn, day):
    row = store_conn.execute(select(usage_daily_reports).where(usage_daily_reports.c.report_date == day.isoformat())).mappings().first()
    return row


def _load(value):
    return json.loads(value) if isinstance(value, str) else value


def _carry_forward(report, store_conn):
    prev = _previous(store_conn, report.day)
    if not prev:
        return
    report.revision = prev["revision"] + 1
    old = _load(prev["provenance"])
    for key in CARRY_KEYS:
        column = KEY_TO_COLUMN[key]
        if report.metrics[key].value is None and old.get(column, {}).get("value") is not None:
            o = old[column]
            report.metrics[key] = Metric(o["value"], o["kind"], o["source"], datetime.fromisoformat(o["collected_at"]),
                                         datetime.fromisoformat(o["period_start"]), datetime.fromisoformat(o["period_end"]),
                                         f"retained from revision {prev['revision']}; latest attempt failed")
            report.warnings.append(f"{column}: latest attempt failed, kept value from revision {prev['revision']}")


def _finalise(report, store_conn, settings, window, today):
    m = report.metrics
    costs = {k: m[k] for k in costing.COST_KEYS}
    total, report.billing_type, missing = costing.total_and_type(costs)
    v = {KEY_TO_COLUMN[k]: m[k].value for k in KEY_TO_COLUMN}
    v["Date"] = report.day.isoformat()
    v["Total Daily Cost (USD)"] = total
    v["Cost per STT-Completed Call (USD)"] = costing.cost_per_stt_call(total, m["calls_stt_completed"].value)
    v["Billing Data Type"] = report.billing_type
    report.values = v

    history = _history(store_conn, report.day)
    report.flags = quality.evaluate_flags(v, history, settings)
    notes = []
    if missing and total is not None:
        notes.append(f"PARTIAL TOTAL - excludes unavailable: {', '.join(missing)}")
    if any(k in m and m[k].kind == ESTIMATED for k in costing.COST_KEYS):
        est = [costing.LABELS[k] for k in costing.COST_KEYS if m[k].kind == ESTIMATED]
        notes.append(f"estimated: {', '.join(est)}")
    if today and report.day >= today:
        notes.append("PARTIAL DAY - the reporting day is not over; re-run tomorrow")
    if m["supabase_db_size"].value is not None and today and report.day < today:
        notes.append("DB size is read at collection time, not end of that day")
    if not window.is_utc_aligned:
        notes.append(f"report timezone {window.tz_name}: provider billing buckets are UTC, so provider costs are unavailable")
    notes.append("cost per STT call = total daily cost / calls STT completed (all tracked services)")
    # Metrics that could not be obtained are listed by name, never silently blank.
    gaps = [KEY_TO_COLUMN[k] for k in KEY_TO_COLUMN if m[k].value is None]
    if gaps:
        notes.append("blank (unavailable, NOT zero): " + ", ".join(gaps))
    report.values["Notes / Issues"] = " | ".join(report.flags + notes + report.warnings)


def _history(store_conn, day):
    rows = store_conn.execute(
        select(usage_daily_reports.c.metric_values).where(usage_daily_reports.c.report_date < day.isoformat())
        .order_by(usage_daily_reports.c.report_date.desc()).limit(7)).all()
    return [_load(r[0]) for r in reversed(rows)]


def persist(report: DailyReport, store_conn):
    """Idempotent upsert keyed by date: re-running updates in place, never inserts a second row."""
    now = now_utc()
    dialect = store_conn.dialect.name
    ins = (postgresql if dialect == "postgresql" else sqlite).insert(usage_daily_reports)
    row = dict(
        report_date=report.day.isoformat(), timezone=report.tz_name, metric_values=report.values,
        provenance={KEY_TO_COLUMN.get(k, k): mt.to_json() for k, mt in report.metrics.items()},
        warnings=report.warnings, flags=report.flags, billing_data_type=report.billing_type,
        revision=report.revision, first_collected_at=now, last_collected_at=now)
    update = {k: getattr(ins.excluded, k) for k in
              ("timezone", "metric_values", "provenance", "warnings", "flags", "billing_data_type", "revision", "last_collected_at")}
    store_conn.execute(ins.on_conflict_do_update(index_elements=["report_date"], set_=update), row)
