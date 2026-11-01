"""Record which Stage 2 values the AI wrote (and with which prompt version)."""
from sqlalchemy import text

from storage.db import engine


def migrate_add_stage2_ai_meta():
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE call_analysis_results ADD COLUMN IF NOT EXISTS fd_stage2_ai_meta JSONB"
        ))


if __name__ == "__main__":
    migrate_add_stage2_ai_meta()
