"""
Database migration: Fix call_recording_date_normalized for rows where call_recording_date is YYYY-MM-DD format.

Background:
- 85 rows have call_recording_date = '19/08/2026' → normalized to '2026-08-19' ✓
- 16 rows have call_recording_date = '2026-08-19' → normalized to NULL ✗
- 2 rows have call_recording_date = NULL → normalized to NULL ✓

Root cause: Migration 004 used datetime.strptime which fails to recognize YYYY-MM-DD format
in certain cases, or the rows were inserted after migration 004 ran but before F2 was updated.

Solution: Directly parse call_recording_date for rows where call_recording_date_normalized IS NULL,
using the same logic as routes/calls.py parse_call_recording_date().

Idempotent and safe to re-run.
"""
from datetime import datetime
from sqlalchemy import text
from storage.db import engine

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y")


def _parse(value):
    """Parse date from various formats."""
    if not value:
        return None
    value = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def migrate_fix_call_recording_date_normalized():
    """Backfill call_recording_date_normalized for rows with NULL normalized date."""
    with engine.begin() as conn:
        # Find rows where call_recording_date_normalized IS NULL but call_recording_date has a value
        rows = conn.execute(text("""
            SELECT id, call_recording_date
            FROM call_analysis_results
            WHERE call_recording_date IS NOT NULL
              AND call_recording_date_normalized IS NULL
        """)).fetchall()

        print(f"Found {len(rows)} rows to backfill")

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
                print(f"  ID {row_id}: '{raw_value}' → {parsed}")
            else:
                print(f"  ID {row_id}: FAILED to parse '{raw_value}'")

        print(f"✓ Backfilled call_recording_date_normalized for {updated} of {len(rows)} row(s)")


if __name__ == "__main__":
    migrate_fix_call_recording_date_normalized()
