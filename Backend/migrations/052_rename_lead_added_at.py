"""Rename fd_leads.created_at to the canonical import timestamp name."""
from sqlalchemy import text
from storage.db import engine


def migrate_rename_lead_added_at():
    # PostgreSQL renames in-place, preserving every historical timestamp,
    # nullability, type, indexes and all lead rows. The catalog guard makes
    # this safe at startup and on a database already migrated manually.
    with engine.begin() as connection:
        connection.execute(text("""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_schema = 'public' AND table_name = 'fd_leads'
                           AND column_name = 'created_at')
                 AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                                 WHERE table_schema = 'public' AND table_name = 'fd_leads'
                                   AND column_name = 'lead_added_at') THEN
                ALTER TABLE fd_leads RENAME COLUMN created_at TO lead_added_at;
              END IF;
            END $$;
        """))


if __name__ == "__main__":
    migrate_rename_lead_added_at()
