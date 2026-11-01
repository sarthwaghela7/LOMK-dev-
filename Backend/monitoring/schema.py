"""Tables owned by the monitoring module (all new; nothing existing altered).

Defined with SQLAlchemy Core so the same definitions create the PostgreSQL
tables (migration 071) and the SQLite tables used by the unit tests.
"""
from sqlalchemy import (
    JSON, BigInteger, Column, DateTime, Float, Index, Integer, MetaData, Numeric,
    String, Table, Text, UniqueConstraint,
)
from sqlalchemy.dialects import postgresql

metadata = MetaData()
_json = JSON().with_variant(postgresql.JSONB(), "postgresql")
_bigint = BigInteger().with_variant(Integer, "sqlite")  # SQLite autoincrement needs INTEGER

# Append-only ledger: one row per paid provider request/attempt. Holds counts and
# identifiers only -- never transcripts, audio, phone numbers or prompts.
provider_usage_events = Table(
    "provider_usage_events", metadata,
    Column("id", _bigint, primary_key=True, autoincrement=True),
    Column("event_key", String(80), nullable=False),          # idempotency key
    Column("provider", String(20), nullable=False),           # 'elevenlabs' | 'openai'
    Column("operation", String(40), nullable=False),          # 'stt' | 'chat_completion'
    Column("status", String(10), nullable=False),             # 'success' | 'failed'
    Column("call_id", String(40)),
    Column("model", String(80)),                              # model name returned by the provider
    Column("audio_seconds", Numeric(12, 3)),
    Column("input_tokens", Integer),
    Column("cached_input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("http_status", Integer),
    Column("error_class", String(80)),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("event_key", name="uq_provider_usage_events_key"),
    Index("ix_provider_usage_events_occurred", "occurred_at"),
)

# One row per reporting day; re-running updates it in place (revision++).
usage_daily_reports = Table(
    "usage_daily_reports", metadata,
    Column("report_date", String(10), primary_key=True),      # ISO date in report_tz
    Column("timezone", String(40), nullable=False),
    Column("metric_values", _json, nullable=False),           # workbook column -> value
    Column("provenance", _json, nullable=False),              # column -> Metric json
    Column("warnings", _json, nullable=False),
    Column("flags", _json, nullable=False),
    Column("billing_data_type", String(20), nullable=False),
    Column("revision", Integer, nullable=False, default=1),
    Column("first_collected_at", DateTime(timezone=True), nullable=False),
    Column("last_collected_at", DateTime(timezone=True), nullable=False),
)

# Month-to-date figures from providers that only expose cumulative totals.
# Daily cost = difference of two consecutive snapshots in the same period.
provider_cumulative_snapshots = Table(
    "provider_cumulative_snapshots", metadata,
    Column("provider", String(20), nullable=False),
    Column("metric", String(40), nullable=False),
    Column("period_start", String(10), nullable=False),       # billing period start (ISO date)
    Column("as_of_date", String(10), nullable=False),         # reporting day the cumulative covers through
    Column("cumulative_usd", Float, nullable=False),
    Column("collected_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("provider", "metric", "period_start", "as_of_date", name="uq_provider_cumulative"),
)
