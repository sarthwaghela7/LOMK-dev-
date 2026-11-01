"""Add an optional, exact call_id link to Missed Call / Non-Responsive tasks.

Every existing "tasks" row is manually logged (imported from a spreadsheet or
typed in) with no reference to a real Call row -- routes/flintdesk/tasks.py
finds the call it describes with a best-effort phone+nearest-date match
(_match_call_for_task / _closest_call). That heuristic stays exactly as-is
for those rows.

Calls auto-created as Missed Call tasks from real Call rows (dst = 602 or
2243860601, see services/missed_call_sync.py) come from a real Call the
moment they're created, so they can and should be linked exactly instead of
re-guessing which Call they mean every time they're displayed. This column
carries that exact link when it's known; NULL (the default, and the value
for every pre-existing row) falls back to the original heuristic unchanged.

This migration is intentionally NOT wired into main.py's
apply_required_flintdesk_migrations() yet. Adding the column is harmless on
its own (nullable, no data migration, no behavior change until something
actually sets it), but it is being held back alongside migration 048 as part
of the same "build the code, don't touch the shared production database
without an explicit go-ahead" decision.

Usage:
    python3 migrations/049_add_fd_m_tasks_call_id.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from storage.db import engine


def migrate_add_fd_m_tasks_call_id():
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE tasks
                ADD COLUMN IF NOT EXISTS fd_m_tasks_call_id UUID REFERENCES calls(call_id) ON DELETE SET NULL
                """
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_tasks_fd_m_tasks_call_id ON tasks (fd_m_tasks_call_id)")
        )


if __name__ == "__main__":
    migrate_add_fd_m_tasks_call_id()
