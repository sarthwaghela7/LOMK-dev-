"""Create Central-Time daily extension assignments for FlintDesk agents."""

from sqlalchemy import text
from storage.db import engine


def migrate_create_fd_assignments():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS fd_assignments (
                id UUID PRIMARY KEY,
                assignment_date DATE NOT NULL,
                extension TEXT NOT NULL,
                number TEXT NOT NULL,
                agent_id UUID NOT NULL REFERENCES fd_users(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ NOT NULL,
                CONSTRAINT fd_assignments_date_extension_unique UNIQUE (assignment_date, extension)
            )
        """))
        print("✓ Ensured fd_assignments exists")


if __name__ == "__main__":
    migrate_create_fd_assignments()
