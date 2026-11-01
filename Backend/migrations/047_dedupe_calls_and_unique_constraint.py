"""Collapse duplicate call rows and enforce F1's dedup key at the DB level.

F1's insert path (_insert_calls in services/Database_sync/f1.py) only ever
checked "does this (call_date, call_time, src, dst, billsec) already exist"
in application code before inserting -- there was no database-level
uniqueness constraint backing that check. If more than one process was ever
running F1 against the same database at once (e.g. an old Render instance
overlapping a new one during a deploy), both could read the same
not-yet-committed "existing" set and both insert, producing duplicate call
rows. This was confirmed in production: a single batch of ~15 calls from
2026-09-15 was reinserted as brand-new rows roughly every 1-2 minutes for an
extended period, leaving 1000+ duplicate rows (~15% of the table) with the
same call_date/call_time/src/dst/billsec but different call_id/call_number.

This migration is idempotent and safe to run on every startup (matching this
repo's convention of auto-running all migrations from main.py): once no
duplicates remain, every step here is a no-op.
"""

from sqlalchemy import text

from storage.db import engine


def migrate_dedupe_calls_and_unique_constraint():
    with engine.begin() as connection:
        # Cleanup can touch thousands of rows across three tables; give it
        # more room than the shared 30s statement_timeout (same reasoning as
        # migrations/044_add_performance_indexes.py).
        connection.execute(text("SET LOCAL statement_timeout = '120000'"))

        # For each group of exact-duplicate calls, pick one survivor:
        # prefer a row an agent has actually touched (front_desk_person_name
        # or any fd_m_* manual field is set) over the default "earliest
        # call_number" rule, so cleanup can never silently discard manually
        # entered data. Falls back to the earliest call_number when no row
        # in the group has manual data (the common case for this incident).
        connection.execute(text("""
            CREATE TEMP TABLE _call_dupe_ranked AS
            SELECT
                c.call_id,
                row_number() OVER (
                    PARTITION BY c.call_date, c.call_time, c.src, c.dst, c.billsec
                    ORDER BY
                        (car.front_desk_person_name IS NOT NULL
                            OR car.fd_m_self_action_item IS NOT NULL
                            OR car.fd_m_verification_comment IS NOT NULL
                            OR car.fd_m_dependency IS NOT NULL
                            OR car.fd_m_manual_action_status IS NOT NULL
                            OR car.fd_m_blocker_comment IS NOT NULL
                            OR car.fd_m_follow_up_dates IS NOT NULL
                            OR car.fd_m_case_status_change IS NOT NULL
                        ) DESC,
                        c.call_number ASC
                ) AS rn
            FROM calls c
            LEFT JOIN call_analysis_results car ON car.call_id = c.call_id
            WHERE (c.call_date, c.call_time, c.src, c.dst, c.billsec) IN (
                SELECT call_date, call_time, src, dst, billsec
                FROM calls
                GROUP BY call_date, call_time, src, dst, billsec
                HAVING count(*) > 1
            )
        """))

        non_survivors = connection.execute(text(
            "SELECT call_id FROM _call_dupe_ranked WHERE rn > 1"
        )).fetchall()
        non_survivor_ids = [row[0] for row in non_survivors]

        if non_survivor_ids:
            # call_analysis_results.call_id -> calls.call_id is ON DELETE
            # RESTRICT, so it must be cleared before the call row can go.
            # analysis_jobs cascades automatically; fd_leads sets its
            # reference to NULL automatically -- neither needs a manual step.
            connection.execute(
                text("DELETE FROM call_analysis_results WHERE call_id = ANY(:ids)"),
                {"ids": non_survivor_ids},
            )
            connection.execute(
                text("DELETE FROM calls WHERE call_id = ANY(:ids)"),
                {"ids": non_survivor_ids},
            )

        connection.execute(text("DROP TABLE _call_dupe_ranked"))

        # Now that no duplicates remain, make F1's own dedup key impossible
        # to violate again, regardless of how many processes ever race an
        # insert against this table concurrently.
        constraint_exists = connection.execute(text(
            "SELECT 1 FROM pg_constraint WHERE conname = 'uq_calls_date_time_src_dst_billsec'"
        )).first()
        if not constraint_exists:
            connection.execute(text("""
                ALTER TABLE calls
                ADD CONSTRAINT uq_calls_date_time_src_dst_billsec
                UNIQUE (call_date, call_time, src, dst, billsec)
            """))
