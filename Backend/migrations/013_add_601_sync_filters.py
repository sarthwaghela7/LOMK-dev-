"""Allow incoming calls on number 601 for extensions 101 and 102."""

from uuid import uuid4

from sqlalchemy import text
from storage.db import engine


FILTERS = [
    ("number", "601", None),
    ("extension", "101", "601"),
    ("extension", "102", "601"),
]


def migrate_add_601_sync_filters():
    with engine.begin() as conn:
        for filter_type, value, parent_number in FILTERS:
            # fd_sync_filters' real identity constraint is the expression
            # index on (filter_type, value, COALESCE(parent_number, '')) --
            # see migration 025. The plain-column UNIQUE(filter_type, value,
            # parent_number) constraint from migration 011 still exists
            # alongside it, but standard SQL NULL semantics mean it never
            # reports a conflict for rows with a NULL parent_number (like
            # this '601' number row), so an ON CONFLICT targeting it lets a
            # duplicate INSERT through to fail against the expression index
            # instead. Checking existence with NOT EXISTS avoids depending on
            # which arbiter Postgres picks entirely.
            conn.execute(
                text(
                    """
                    INSERT INTO fd_sync_filters (id, filter_type, value, parent_number)
                    SELECT :id, :filter_type, :value, :parent_number
                    WHERE NOT EXISTS (
                        SELECT 1 FROM fd_sync_filters
                        WHERE filter_type = :filter_type
                          AND value = :value
                          AND COALESCE(parent_number, '') = COALESCE(:parent_number, '')
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "filter_type": filter_type,
                    "value": value,
                    "parent_number": parent_number,
                },
            )

        print("Ensured Vitel sync filters for number 601 and extensions 101/102")


if __name__ == "__main__":
    migrate_add_601_sync_filters()
