"""Make extension assignments persistent until changed or removed."""

from sqlalchemy import text

from storage.db import engine


def migrate_persistent_fd_assignments():
    with engine.begin() as connection:
        # expires_at must allow NULL before the backfill below sets it to NULL
        # for active assignments.
        connection.execute(text("ALTER TABLE fd_assignments ALTER COLUMN expires_at DROP NOT NULL"))
        # Initialize legacy rows only when the column is first introduced.
        # Re-running startup migrations must not reactivate a removed row.
        connection.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'fd_assignments' AND column_name = 'is_active'
                ) THEN
                    ALTER TABLE fd_assignments ADD COLUMN is_active BOOLEAN;
                    UPDATE fd_assignments SET is_active = FALSE;
                    WITH latest AS (
                        SELECT DISTINCT ON (number, extension) id
                        FROM fd_assignments
                        ORDER BY number, extension, created_at DESC, id DESC
                    )
                    UPDATE fd_assignments assignment
                    SET is_active = TRUE, expires_at = NULL
                    FROM latest
                    WHERE assignment.id = latest.id;
                    ALTER TABLE fd_assignments ALTER COLUMN is_active SET DEFAULT FALSE;
                    ALTER TABLE fd_assignments ALTER COLUMN is_active SET NOT NULL;
                END IF;
            END $$
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_fd_assignments_active ON fd_assignments (is_active, agent_id, extension)"))


if __name__ == "__main__":
    migrate_persistent_fd_assignments()
