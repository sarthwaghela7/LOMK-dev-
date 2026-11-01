"""Add a manual display override for the AI-extracted client name."""

from sqlalchemy import text

from storage.db import engine


def migrate_add_manual_client_name():
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE call_analysis_results ADD COLUMN IF NOT EXISTS fd_m_client_name TEXT"
        ))
