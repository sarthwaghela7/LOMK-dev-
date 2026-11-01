"""Persist Leads S3 completion state and timestamp."""

from sqlalchemy import text

from storage.db import engine


def migrate_lead_completion_fields():
    with engine.begin() as connection:
        connection.execute(text("""
            ALTER TABLE fd_leads
              ADD COLUMN IF NOT EXISTS s3_completed BOOLEAN NOT NULL DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS s3_completed_at TIMESTAMPTZ
        """))


if __name__ == "__main__":
    migrate_lead_completion_fields()
