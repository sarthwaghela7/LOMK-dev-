"""Persist the time a FlintDesk case is marked completed."""

from sqlalchemy import text

from storage.db import engine


def migrate_case_completion_timestamp():
    with engine.begin() as connection:
        connection.execute(text("""
            ALTER TABLE call_analysis_results
            ADD COLUMN IF NOT EXISTS case_closing_completed_at TIMESTAMPTZ
        """))


if __name__ == "__main__":
    migrate_case_completion_timestamp()
