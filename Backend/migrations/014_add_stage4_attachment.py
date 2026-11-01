"""Add the Stage 4 attachment reference to call analysis results."""

from sqlalchemy import text

from storage.db import engine


def migrate_add_stage4_attachment():
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE call_analysis_results "
                "ADD COLUMN IF NOT EXISTS fd_m_stage4_attachment TEXT"
            )
        )
        print("Ensured call_analysis_results.fd_m_stage4_attachment exists")


if __name__ == "__main__":
    migrate_add_stage4_attachment()