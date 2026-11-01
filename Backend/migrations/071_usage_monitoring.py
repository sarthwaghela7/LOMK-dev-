"""Create the usage-monitoring tables (new tables only; no existing table is altered).

provider_usage_events          append-only ledger of paid STT / LLM requests
usage_daily_reports            one row per reporting day (idempotent upsert)
provider_cumulative_snapshots  month-to-date billing snapshots (daily = delta)

Row-level security is enabled with no client policy, like analysis_jobs: only
the backend's database owner/service role can read them.
"""
from sqlalchemy import text

from monitoring.schema import metadata
from storage.db import engine


def migrate_usage_monitoring():
    with engine.begin() as conn:
        metadata.create_all(conn, checkfirst=True)
        for table in metadata.tables:
            conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
