from sqlalchemy import text

from storage.db import engine


def migrate_create_todos():
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS todos (
                id UUID PRIMARY KEY,
                title TEXT NOT NULL CHECK (length(trim(title)) > 0),
                description TEXT,
                status TEXT NOT NULL DEFAULT 'To Do'
                    CHECK (status IN ('To Do', 'In Progress', 'Completed', 'Blocker', 'Dropped')),
                entry_date DATE NOT NULL DEFAULT CURRENT_DATE,
                due_date DATE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_todos_status_created_at ON todos(status, created_at)"))


if __name__ == "__main__":
    migrate_create_todos()
