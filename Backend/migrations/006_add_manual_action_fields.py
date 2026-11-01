"""
Database migration: add FlintDesk manual (agent-entered) action-tracking
columns to call_analysis_results.

These are NOT LLM-generated, so unlike other analysis fields they are not
registered in the `prompts` table.

Adds:
  self_action_item     TEXT
  action_due_date       DATE
  manual_action_status  TEXT
  dependency            TEXT
  follow_up_dates       TEXT
  follow_up_attempts    INTEGER
  blocker_comment       TEXT

Idempotent and safe to re-run.
"""
from sqlalchemy import text
from storage.db import engine


def migrate_add_manual_action_fields():
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE call_analysis_results "
            "ADD COLUMN IF NOT EXISTS self_action_item TEXT, "
            "ADD COLUMN IF NOT EXISTS action_due_date DATE, "
            "ADD COLUMN IF NOT EXISTS manual_action_status TEXT, "
            "ADD COLUMN IF NOT EXISTS dependency TEXT, "
            "ADD COLUMN IF NOT EXISTS follow_up_dates TEXT, "
            "ADD COLUMN IF NOT EXISTS follow_up_attempts INTEGER, "
            "ADD COLUMN IF NOT EXISTS blocker_comment TEXT"
        ))
        print("✓ Ensured manual action-tracking columns on call_analysis_results")


if __name__ == "__main__":
    migrate_add_manual_action_fields()
    print("\n✓ Migration complete!")
