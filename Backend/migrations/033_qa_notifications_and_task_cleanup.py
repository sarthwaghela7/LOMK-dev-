"""Add notification context and make submitted Task deletion safe."""

from sqlalchemy import text

from storage.db import engine


def migrate_qa_notifications_and_task_cleanup():
    with engine.begin() as connection:
        connection.execute(text("""
            ALTER TABLE task_notifications
              ADD COLUMN IF NOT EXISTS source_page TEXT,
              ADD COLUMN IF NOT EXISTS entity_id TEXT,
              ADD COLUMN IF NOT EXISTS change_context JSONB
        """))
        connection.execute(text("""
            DO $$
            DECLARE constraint_name text;
            BEGIN
                SELECT conname INTO constraint_name
                FROM pg_constraint
                WHERE conrelid = 'task_notifications'::regclass
                  AND contype = 'f'
                  AND confrelid = 'tasks'::regclass;

                IF constraint_name IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE task_notifications DROP CONSTRAINT %I', constraint_name);
                END IF;

                ALTER TABLE task_notifications
                ADD CONSTRAINT task_notifications_task_id_fkey
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL;
            END $$;
        """))
        connection.execute(text("""
            DO $$
            DECLARE constraint_name text;
            BEGIN
                SELECT conname INTO constraint_name
                FROM pg_constraint
                WHERE conrelid = 'task_notifications'::regclass
                  AND contype = 'f'
                  AND confrelid = 'fd_users'::regclass;

                IF constraint_name IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE task_notifications DROP CONSTRAINT %I', constraint_name);
                END IF;

                ALTER TABLE task_notifications
                ADD CONSTRAINT task_notifications_recipient_id_fkey
                FOREIGN KEY (recipient_id) REFERENCES fd_users(id) ON DELETE CASCADE;
            END $$;
        """))


if __name__ == "__main__":
    migrate_qa_notifications_and_task_cleanup()
