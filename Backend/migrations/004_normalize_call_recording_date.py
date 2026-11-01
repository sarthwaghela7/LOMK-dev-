"""
Database migration: normalize call_recording_date into a real Date column.

call_analysis_results.call_recording_date is a Text column with mixed
formats (YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY) inherited from older data, so
every endpoint that filters/sorts by date (Pending Cases, Success Cases,
Business, Report Analysis, etc.) has to pull ALL rows into Python and parse
each one before it can filter or sort — no index can be used on a Text
column with inconsistent formats.

This adds `call_recording_date_normalized` (a real Date column), backfills
it from the existing text column using the same 3 formats the app already
tries elsewhere (parse_call_recording_date in routes/calls.py), and indexes
it so future queries can filter/sort in SQL instead of in Python.

Idempotent and safe to re-run.
"""
from datetime import datetime

from sqlalchemy import text
from storage.db import engine

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y")


def _parse(value):
    if not value:
        return None
    value = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def migrate_normalize_call_recording_date():
    with engine.begin() as conn:
        conn.execute(text("""
            ALTER TABLE call_analysis_results
            ADD COLUMN IF NOT EXISTS call_recording_date_normalized DATE
        """))
        print("✓ Ensured column call_recording_date_normalized")

        rows = conn.execute(text("""
            SELECT id, call_recording_date
            FROM call_analysis_results
            WHERE call_recording_date IS NOT NULL
              AND call_recording_date_normalized IS NULL
        """)).fetchall()

        updated = 0
        for row_id, raw_value in rows:
            parsed = _parse(raw_value)
            if parsed:
                conn.execute(
                    text("""
                        UPDATE call_analysis_results
                        SET call_recording_date_normalized = :parsed
                        WHERE id = :row_id
                    """),
                    {"parsed": parsed, "row_id": row_id},
                )
                updated += 1
        print(f"✓ Backfilled call_recording_date_normalized for {updated} of {len(rows)} row(s)")

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_call_analysis_results_call_recording_date_normalized
            ON call_analysis_results (call_recording_date_normalized)
        """))
        print("✓ Ensured index ix_call_analysis_results_call_recording_date_normalized")


if __name__ == "__main__":
    migrate_normalize_call_recording_date()
    print("\n✓ Migration complete!")
