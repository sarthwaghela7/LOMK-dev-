"""
Database migration: rename FlintDesk manual action-tracking columns on
call_analysis_results to be prefixed with "fd_m_".

  self_action_item      -> fd_m_self_action_item
  action_due_date       -> fd_m_action_due_date
  manual_action_status  -> fd_m_manual_action_status
  dependency            -> fd_m_dependency
  follow_up_dates       -> fd_m_follow_up_dates
  follow_up_attempts    -> fd_m_follow_up_attempts
  blocker_comment       -> fd_m_blocker_comment

Idempotent and safe to re-run.
"""
from sqlalchemy import text
from storage.db import engine

RENAMES = {
    "self_action_item": "fd_m_self_action_item",
    "action_due_date": "fd_m_action_due_date",
    "manual_action_status": "fd_m_manual_action_status",
    "dependency": "fd_m_dependency",
    "follow_up_dates": "fd_m_follow_up_dates",
    "follow_up_attempts": "fd_m_follow_up_attempts",
    "blocker_comment": "fd_m_blocker_comment",
}


def migrate_rename_manual_fields():
    with engine.begin() as conn:
        existing_cols = {
            row[0]
            for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'call_analysis_results'"
            ))
        }
        for old_name, new_name in RENAMES.items():
            if new_name in existing_cols:
                print(f"✓ {new_name} already exists")
                continue
            if old_name not in existing_cols:
                print(f"! {old_name} not found, skipping")
                continue
            conn.execute(text(
                f"ALTER TABLE call_analysis_results RENAME COLUMN {old_name} TO {new_name}"
            ))
            print(f"✓ Renamed {old_name} -> {new_name}")


if __name__ == "__main__":
    migrate_rename_manual_fields()
    print("\n✓ Migration complete!")
