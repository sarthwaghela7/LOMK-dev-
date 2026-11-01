"""Google Sheet lead sync: Needs column, Lead ID sequence, source-row ledger.

Purely additive. ``ld_lead_number_seq`` supplies the number for system-
generated ``LD#N`` Lead IDs and is positioned after any existing ``LD#N`` ID so
a generated ID can never collide with a historical one. ``fd_lead_sheet_rows``
records every sheet row seen (keyed by a stable hash of its identity cells) so
re-reading the same row never creates a second lead.
"""
from sqlalchemy import text
from storage.db import engine


def migrate_lead_sheet_sync():
    with engine.begin() as c:
        c.execute(text("ALTER TABLE fd_leads ADD COLUMN IF NOT EXISTS fd_m_leads_needs TEXT"))
        c.execute(text("CREATE SEQUENCE IF NOT EXISTS ld_lead_number_seq START 1 INCREMENT 1"))
        # Only position the sequence while it has never been used, so a re-run
        # can never move it backwards.
        c.execute(text("""
            SELECT setval(
              'ld_lead_number_seq',
              COALESCE((SELECT max(substring(fd_m_leads_lead_id from '^LD#([0-9]{1,15})$')::bigint)
                        FROM fd_leads), 0) + 1,
              false)
            WHERE NOT (SELECT is_called FROM ld_lead_number_seq)
        """))
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS fd_lead_sheet_rows (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              row_key TEXT NOT NULL UNIQUE,
              sheet_row_number INTEGER,
              lead_id UUID REFERENCES fd_leads(id) ON DELETE SET NULL,
              status TEXT NOT NULL CHECK (status IN ('imported', 'error')),
              error TEXT,
              first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_fd_lead_sheet_rows_status ON fd_lead_sheet_rows(status)"))


if __name__ == "__main__":
    migrate_lead_sheet_sync()
