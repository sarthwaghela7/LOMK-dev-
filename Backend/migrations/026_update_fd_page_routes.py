"""Rename extension page routes to their current FlintDesk destinations."""

from sqlalchemy import text

from storage.db import engine


def migrate_update_fd_page_routes():
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE fd_sync_filters
            SET target_page = CASE
                WHEN target_page = 'dashboard' THEN 'leads'
                WHEN target_page = 'process_calls' OR target_page IS NULL THEN 'calls'
                ELSE target_page
            END
            WHERE filter_type = 'extension'
        """))


if __name__ == "__main__":
    migrate_update_fd_page_routes()
