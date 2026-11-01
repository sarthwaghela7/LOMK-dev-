from sqlalchemy import text
from storage.db import engine


def migrate_remove_legacy_call_analysis_leads():
    """Remove only the superseded Leads JSON payload after fd_leads migration."""
    with engine.begin() as c:
        c.execute(text("ALTER TABLE call_analysis_results DROP COLUMN IF EXISTS fd_m_leads"))


if __name__ == '__main__':
    migrate_remove_legacy_call_analysis_leads()
