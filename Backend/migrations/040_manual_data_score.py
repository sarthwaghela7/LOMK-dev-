from sqlalchemy import text

from storage.db import engine


def migrate_manual_data_score():
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE call_analysis_results ADD COLUMN IF NOT EXISTS manual_data_score DOUBLE PRECISION"))
        connection.execute(text("ALTER TABLE call_analysis_results ADD COLUMN IF NOT EXISTS manual_data_score_reason TEXT"))
        connection.execute(text("ALTER TABLE call_analysis_results ADD COLUMN IF NOT EXISTS manual_data_scored_at TIMESTAMPTZ"))
        connection.execute(text("""
            DO $$ BEGIN
              ALTER TABLE call_analysis_results
              ADD CONSTRAINT ck_manual_data_score_range
              CHECK (manual_data_score IS NULL OR (manual_data_score >= 0 AND manual_data_score <= 10));
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))


if __name__ == "__main__":
    migrate_manual_data_score()
