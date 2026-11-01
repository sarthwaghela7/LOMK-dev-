from sqlalchemy import text

from storage.db import engine


def migrate_add_fd_m_leads():
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE call_analysis_results ADD COLUMN IF NOT EXISTS fd_m_leads JSONB"
        ))


if __name__ == "__main__":
    migrate_add_fd_m_leads()
