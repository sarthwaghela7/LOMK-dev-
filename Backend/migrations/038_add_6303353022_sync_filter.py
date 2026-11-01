"""Add the 6303353022 Vital line to the existing F1 sync allow-list."""

import sys
from pathlib import Path
from uuid import uuid4

# This migration is intentionally runnable both as a startup import and as
# ``python3 migrations/038_add_6303353022_sync_filter.py`` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from storage.db import engine


def migrate_add_6303353022_sync_filter():
    """Idempotently enable F1/client/UI number resolution for this line."""
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO fd_sync_filters (id, filter_type, value, parent_number)
                VALUES (:id, 'number', '6303353022', NULL)
                ON CONFLICT DO NOTHING
                """
            ),
            {"id": uuid4()},
        )


if __name__ == "__main__":
    migrate_add_6303353022_sync_filter()
