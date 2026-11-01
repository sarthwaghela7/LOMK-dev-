from sqlalchemy import text

from storage.db import engine


def migrate_add_todo_owner():
    """To-dos are private to the FlintDesk user who created them.

    Rows created before this migration have no known owner and stay NULL, so
    they are visible to nobody until an admin assigns them.
    """
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE todos ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES fd_users(id) ON DELETE CASCADE"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_todos_owner_status ON todos(owner_id, status, created_at)"))


if __name__ == "__main__":
    migrate_add_todo_owner()
