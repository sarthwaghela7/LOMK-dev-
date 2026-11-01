"""Persist the optional note stored with a FlintDesk Action Due Date."""
from sqlalchemy import text
from storage.db import engine


def migrate_action_due_note():
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE call_analysis_results ADD COLUMN IF NOT EXISTS fd_m_action_due_note TEXT"))


if __name__ == "__main__":
    migrate_action_due_note()
