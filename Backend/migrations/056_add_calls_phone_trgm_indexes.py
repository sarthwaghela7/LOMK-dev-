"""Trigram indexes on calls.src/calls.dst for phone-number LIKE lookups.

Several places match a call by the last 10 digits of a phone number via
Call.src.like(f"%{digits}")/Call.dst.like(f"%{digits}") -- see
_match_call_for_task (routes/flintdesk/tasks.py, pre-existing) and the
newer phone-scoped queries added to stop full-table Call scans (same file,
and routes/flintdesk/leads.py). A leading-wildcard LIKE can't use a plain
B-tree index (ix_calls_call_date_call_time doesn't help it either), so
every one of those queries was still a sequential scan over the whole
calls table -- the phone-number filter cut down how many rows got
hydrated into Python, but not how long the database itself took to find
them, which is the connection-hold time that actually matters for pool
exhaustion. pg_trgm's GIN index supports substring/suffix LIKE queries
directly. This is index-only: no application query needed to change.

Not registered in main.py's startup migration list on purpose: it must
run out-of-band (see __main__ below), not on the request-serving path.
It builds each index with CREATE INDEX CONCURRENTLY, which (unlike a
plain CREATE INDEX) does not take a lock that blocks concurrent reads
or writes on `calls` while it builds -- important on a table this size
that's under continuous read/write load. CONCURRENTLY cannot run inside
a transaction block, so this connects with autocommit rather than using
the shared `engine`/`SessionLocal` (both wrap statements in a
transaction).
"""
import os

import psycopg2


def migrate_add_calls_phone_trgm_indexes():
    url = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
    connection = psycopg2.connect(url, connect_timeout=10)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            cursor.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_calls_src_trgm ON calls USING gin (src gin_trgm_ops)"
            )
            cursor.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_calls_dst_trgm ON calls USING gin (dst gin_trgm_ops)"
            )
    finally:
        connection.close()


if __name__ == "__main__":
    migrate_add_calls_phone_trgm_indexes()
