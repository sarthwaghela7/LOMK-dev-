"""Deduplicate extension config and add per-extension page routing."""

from sqlalchemy import text

from storage.db import engine


def migrate_fix_fd_assignment_config():
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE fd_sync_filters ADD COLUMN IF NOT EXISTS target_page TEXT"
        ))
        connection.execute(text("""
            UPDATE fd_sync_filters
            SET target_page = 'process_calls'
            WHERE filter_type = 'extension' AND target_page IS NULL
        """))
        connection.execute(text("""
            DELETE FROM fd_sync_filters duplicate
            USING fd_sync_filters keeper
            WHERE duplicate.id > keeper.id
              AND duplicate.filter_type = keeper.filter_type
              AND duplicate.value = keeper.value
              AND COALESCE(duplicate.parent_number, '') = COALESCE(keeper.parent_number, '')
        """))
        connection.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_fd_sync_filters_identity
            ON fd_sync_filters (filter_type, value, COALESCE(parent_number, ''))
        """))
        connection.execute(text("""
            DELETE FROM fd_assignments
            WHERE agent_id IN (
                SELECT id FROM fd_users WHERE lower(trim(name)) = 'manaswi'
            )
        """))
        connection.execute(text("""
            UPDATE fd_users SET is_active = FALSE
            WHERE lower(trim(name)) = 'manaswi'
        """))


if __name__ == "__main__":
    migrate_fix_fd_assignment_config()
