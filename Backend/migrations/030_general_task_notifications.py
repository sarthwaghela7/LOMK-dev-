from sqlalchemy import text

from storage.db import engine


def migrate_general_task_notifications():
    """Add the minimum persistence needed for General Task ownership alerts."""
    with engine.begin() as connection:
        connection.execute(text("""
            ALTER TABLE tasks
            ADD COLUMN IF NOT EXISTS assigned_by_id UUID REFERENCES fd_users(id)
        """))
        connection.execute(text("""
            ALTER TABLE task_notifications
            ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT 'Notification'
        """))
        connection.execute(text("""
            ALTER TABLE task_notifications
            ADD COLUMN IF NOT EXISTS notification_type TEXT NOT NULL
            DEFAULT 'general_task_assignment'
        """))
        connection.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_task_notifications_recipient_created
            ON task_notifications (recipient_id, created_at DESC)
        """))


if __name__ == "__main__":
    migrate_general_task_notifications()
