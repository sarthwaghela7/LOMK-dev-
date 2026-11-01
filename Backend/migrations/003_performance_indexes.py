"""
Database migration: performance indexes for call-record/report queries.

The Dashboard and Report pages fetch calls filtered by `calls.call_date`
(often the whole page's slowest query on a growing table since it had no
index — every request was a full sequential scan + sort). This adds an
index on that column, plus one on `call_analysis_results.call_id` /
`call_number` since those are looked up on every request that joins
calls to their analysis rows.
"""
from sqlalchemy import text
from storage.db import engine


def migrate_performance_indexes():
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_calls_call_date ON calls (call_date)"
        ))
        print("✓ Ensured index ix_calls_call_date")

        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_call_analysis_results_call_number "
            "ON call_analysis_results (call_number)"
        ))
        print("✓ Ensured index ix_call_analysis_results_call_number")

        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_call_analysis_results_call_reference "
            "ON call_analysis_results (call_reference)"
        ))
        print("✓ Ensured index ix_call_analysis_results_call_reference")


if __name__ == "__main__":
    migrate_performance_indexes()
    print("\n✓ Migration complete!")
