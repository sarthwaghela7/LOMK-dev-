"""Create the FlintDesk F1 sync allow-list for phone numbers and extensions."""

from uuid import uuid4

from sqlalchemy import text
from storage.db import engine


DEFAULT_FILTERS = [
    ("number", "2243374447", None),
    ("extension", "201", "2243374447"),
    ("extension", "202", "2243374447"),
    ("number", "3128781255", None),
    ("extension", "101", "3128781255"),
    ("extension", "102", "3128781255"),
    ("number", "601", None),
    ("extension", "101", "601"),
    ("extension", "102", "601"),
]


def migrate_create_fd_sync_filters():
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS fd_sync_filters (
                    id UUID PRIMARY KEY,
                    filter_type TEXT NOT NULL CHECK (filter_type IN ('number', 'extension')),
                    value TEXT NOT NULL,
                    parent_number TEXT,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    CONSTRAINT fd_sync_filters_group_value_unique UNIQUE (filter_type, value, parent_number)
                )
                """
            )
        )

        conn.execute(text("ALTER TABLE fd_sync_filters ADD COLUMN IF NOT EXISTS parent_number TEXT"))
        conn.execute(text("ALTER TABLE fd_sync_filters DROP CONSTRAINT IF EXISTS fd_sync_filters_type_value_unique"))
        conn.execute(text("ALTER TABLE fd_sync_filters DROP CONSTRAINT IF EXISTS fd_sync_filters_group_value_unique"))
        conn.execute(text("ALTER TABLE fd_sync_filters ADD CONSTRAINT fd_sync_filters_group_value_unique UNIQUE (filter_type, value, parent_number)"))
        conn.execute(text("DELETE FROM fd_sync_filters WHERE filter_type = 'number' AND value = '13128781255'"))
        conn.execute(text("UPDATE fd_sync_filters SET parent_number = '3128781255' WHERE filter_type = 'extension' AND value IN ('101', '102') AND parent_number IS NULL"))

        for filter_type, value, parent_number in DEFAULT_FILTERS:
            conn.execute(
                text(
                    """
                    INSERT INTO fd_sync_filters (id, filter_type, value, parent_number)
                    VALUES (:id, :filter_type, :value, :parent_number)
                    ON CONFLICT (filter_type, value, COALESCE(parent_number, '')) DO NOTHING
                    """
                ),
                {"id": uuid4(), "filter_type": filter_type, "value": value, "parent_number": parent_number},
            )

        print("✓ Ensured fd_sync_filters exists and retained the current F1 defaults")


if __name__ == "__main__":
    migrate_create_fd_sync_filters()
