"""
Database migration: Enforce ONE call_analysis_results row per call.

Some calls were re-queued for processing more than once (e.g. after a
late/corrected audio match reset calls.processing_status back to
"not processed"), and the F2 processor previously always INSERTed a new
call_analysis_results row instead of updating the existing one. That left
duplicate rows for the same call_id sitting in the table, which is exactly
what caused CallRecords, Dashboard, and Reports to disagree on a call's
front_desk_person_name / agent and on per-agent call counts.

This migration is idempotent and safe to re-run:
1. For every call_id with more than one call_analysis_results row, keep
   ONLY the most recently created row (highest created_at, falling back to
   highest id when created_at ties/NULLs) and delete the rest.
2. Add a UNIQUE constraint on call_analysis_results.call_id so this can
   never happen again at the database level, regardless of what the
   application code does.
"""
from sqlalchemy import text
from storage.db import engine


def migrate_dedupe_call_analysis():
    with engine.begin() as conn:
        # 1. Find how many duplicate groups exist (for reporting only)
        dup_groups = conn.execute(text("""
            SELECT call_id, COUNT(*) AS cnt
            FROM call_analysis_results
            GROUP BY call_id
            HAVING COUNT(*) > 1
        """)).fetchall()
        print(f"Found {len(dup_groups)} call_id(s) with duplicate call_analysis_results rows")

        if dup_groups:
            # 2. Delete every row except the most recent one per call_id.
            # "Most recent" = highest created_at, tie-broken by highest id
            # (id is a monotonically increasing PK, so it's a reliable
            # fallback when created_at is NULL or identical).
            result = conn.execute(text("""
                DELETE FROM call_analysis_results
                WHERE id NOT IN (
                    SELECT DISTINCT ON (call_id) id
                    FROM call_analysis_results
                    ORDER BY call_id, created_at DESC NULLS LAST, id DESC
                )
            """))
            print(f"✓ Deleted {result.rowcount} duplicate call_analysis_results row(s)")
        else:
            print("✓ No duplicates found, nothing to delete")

        # 3. Add the UNIQUE constraint (skip if it already exists)
        existing_constraint = conn.execute(text("""
            SELECT 1 FROM pg_constraint WHERE conname = 'call_analysis_results_call_id_key'
        """)).fetchone()

        if not existing_constraint:
            conn.execute(text("""
                ALTER TABLE call_analysis_results
                ADD CONSTRAINT call_analysis_results_call_id_key UNIQUE (call_id)
            """))
            print("✓ Added UNIQUE constraint on call_analysis_results.call_id")
        else:
            print("✓ UNIQUE constraint on call_analysis_results.call_id already exists")


if __name__ == "__main__":
    migrate_dedupe_call_analysis()
    print("\n✓ Migration complete!")
