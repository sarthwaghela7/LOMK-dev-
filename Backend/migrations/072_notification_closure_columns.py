"""task_notifications: persistent, individually dismissible closure notifications."""
from sqlalchemy import text

from storage.db import engine


def migrate_notification_closure_columns():
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE task_notifications ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMPTZ"))
        connection.execute(text("ALTER TABLE task_notifications ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ"))
        connection.execute(text(
            "ALTER TABLE task_notifications ADD COLUMN IF NOT EXISTS closure_id UUID "
            "REFERENCES fd_case_closures(id) ON DELETE CASCADE"
        ))
        # One notification per (closure, recipient, kind): duplicates are impossible, whatever retries.
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS task_notifications_closure_recipient "
            "ON task_notifications (closure_id, recipient_id, notification_type) WHERE closure_id IS NOT NULL"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS task_notifications_pending_closures "
            "ON task_notifications (recipient_id) WHERE closure_id IS NOT NULL AND dismissed_at IS NULL"
        ))


if __name__ == "__main__":
    migrate_notification_closure_columns()
