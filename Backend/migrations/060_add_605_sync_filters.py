"""Allow incoming calls on routing number 605 for extensions 203, 204 and 205.

605 is the inbound ring route for lead number 2243939098, the same way 601
routes 3128781255 and 603 routes 2243374447.
"""

from uuid import uuid4

from sqlalchemy import text
from storage.db import engine


FILTERS = [
    ("number", "605", None),
    ("extension", "203", "605"),
    ("extension", "204", "605"),
    ("extension", "205", "605"),
    # Routing leg itself, matching 029's special fallbacks.
    ("extension", "605", "605"),
]


def migrate_add_605_sync_filters():
    with engine.begin() as conn:
        for filter_type, value, parent_number in FILTERS:
            # NOT EXISTS rather than ON CONFLICT: see migration 013.
            conn.execute(
                text(
                    """
                    INSERT INTO fd_sync_filters (id, filter_type, value, parent_number, created_at, target_page)
                    SELECT :id, :filter_type, :value, :parent_number, now(),
                           CASE WHEN :filter_type = 'extension' THEN 'calls' END
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

        print("Ensured Vitel sync filters for number 605 and extensions 203/204/205")


if __name__ == "__main__":
    migrate_add_605_sync_filters()
