"""Allow canonical lead deletion while retaining assignment notifications."""

from sqlalchemy import text

from storage.db import engine


def migrate_lead_notification_delete_behavior():
    with engine.begin() as connection:
        # Migration 031 created the nullable lead reference. Replace only that
        # FK with SET NULL so an Admin can delete a lead without deleting its
        # notification history or being blocked by a restrictive FK.
        connection.execute(text("""
            DO $$
            DECLARE constraint_name text;
            BEGIN
                SELECT conname INTO constraint_name
                FROM pg_constraint
                WHERE conrelid = 'task_notifications'::regclass
                  AND contype = 'f'
                  AND confrelid = 'fd_leads'::regclass;

                IF constraint_name IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE task_notifications DROP CONSTRAINT %I', constraint_name);
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'task_notifications'::regclass
                      AND conname = 'task_notifications_lead_id_fkey'
                ) THEN
                    ALTER TABLE task_notifications
                    ADD CONSTRAINT task_notifications_lead_id_fkey
                    FOREIGN KEY (lead_id) REFERENCES fd_leads(id) ON DELETE SET NULL;
                END IF;
            END $$;
        """))


if __name__ == '__main__':
    migrate_lead_notification_delete_behavior()
