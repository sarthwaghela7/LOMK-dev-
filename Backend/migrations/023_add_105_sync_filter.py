"""Capture calls for lead number 105 and its configured extension."""

from uuid import uuid4

from sqlalchemy import text

from storage.db import engine


SYNC_FILTERS = (
    ("number", "105", None),
    ("extension", "6304452050", "105"),
)


def migrate_add_105_sync_filter():
    with engine.begin() as connection:
        for filter_type, value, parent_number in SYNC_FILTERS:
            connection.execute(
                text(
                    """
                    INSERT INTO fd_sync_filters (id, filter_type, value, parent_number)
                    VALUES (:id, :filter_type, :value, :parent_number)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "id": uuid4(),
                    "filter_type": filter_type,
                    "value": value,
                    "parent_number": parent_number,
                },
            )


if __name__ == "__main__":
    migrate_add_105_sync_filter()
