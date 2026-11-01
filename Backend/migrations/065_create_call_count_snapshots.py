"""Daily call-count snapshots (end-of-day baseline) and their reconciliation state.

Three new tables; nothing existing is altered:

  fd_call_count_snapshot_runs   one row per business date: did the snapshot
                                succeed, when, and the result of the latest
                                re-check. This is the only mutable table.
  fd_call_count_snapshots       the official counts (total / per agent / per
                                extension). Immutable: UPDATE and DELETE are
                                rejected by a trigger.
  fd_call_count_snapshot_calls  which calls made up the count and how each was
                                attributed, so a later mismatch can be traced
                                to the exact calls. Immutable as well.

An intentional administrative correction must disable the trigger explicitly
(ALTER TABLE ... DISABLE TRIGGER); nothing in the application does that.
"""
from sqlalchemy import text

from storage.db import engine


def create_tables(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS fd_call_count_snapshot_runs (
          business_date DATE PRIMARY KEY,
          timezone TEXT NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
          snapshot_at TIMESTAMPTZ,
          late BOOLEAN NOT NULL DEFAULT FALSE,
          total_calls INTEGER CHECK (total_calls IS NULL OR total_calls >= 0),
          attempts INTEGER NOT NULL DEFAULT 1,
          error TEXT,
          last_checked_at TIMESTAMPTZ,
          last_check_status TEXT CHECK (last_check_status IN ('MATCH', 'MISMATCH', 'ERROR')),
          last_check_mismatches INTEGER,
          last_check_error TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS fd_call_count_snapshots (
          business_date DATE NOT NULL
            REFERENCES fd_call_count_snapshot_runs(business_date) ON DELETE RESTRICT,
          dimension TEXT NOT NULL CHECK (dimension IN ('total', 'agent', 'extension')),
          key TEXT NOT NULL,
          call_count INTEGER NOT NULL CHECK (call_count >= 0),
          PRIMARY KEY (business_date, dimension, key)
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS fd_call_count_snapshot_calls (
          business_date DATE NOT NULL
            REFERENCES fd_call_count_snapshot_runs(business_date) ON DELETE RESTRICT,
          call_id TEXT NOT NULL,
          call_reference TEXT,
          agent TEXT NOT NULL,
          extension TEXT NOT NULL,
          PRIMARY KEY (business_date, call_id)
        )
    """))
    conn.execute(text("""
        CREATE OR REPLACE FUNCTION fd_call_count_snapshot_immutable() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'call count snapshots are immutable (% on %)', TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
    """))
    for table in ("fd_call_count_snapshots", "fd_call_count_snapshot_calls"):
        conn.execute(text(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}"))
        conn.execute(text(f"""
            CREATE TRIGGER {table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION fd_call_count_snapshot_immutable()
        """))


def drop_tables(conn):
    """Reverse of create_tables (manual use only; destroys snapshot history)."""
    conn.execute(text("DROP TABLE IF EXISTS fd_call_count_snapshot_calls"))
    conn.execute(text("DROP TABLE IF EXISTS fd_call_count_snapshots"))
    conn.execute(text("DROP TABLE IF EXISTS fd_call_count_snapshot_runs"))
    conn.execute(text("DROP FUNCTION IF EXISTS fd_call_count_snapshot_immutable()"))


def migrate_create_call_count_snapshots():
    with engine.begin() as conn:
        create_tables(conn)


if __name__ == "__main__":
    migrate_create_call_count_snapshots()
