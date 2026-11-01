"""Remove the fd_sync_filters row that made F1 pull dst=604 (Website ring
route -> Hannah AI hand-off) calls and treat them as a missed-call number.

Undoes migrations/053_add_website_hannah_ai_sync_filter.py, at the user's
request: the Website (604) Hannah AI categorization added alongside that
migration is being removed, reverting to the Front Desk Agent-only
(602/2243860601) logic in services/hannah_ai_routing.py that was working
correctly before 604 was added. Leaving this row in place after removing
the categorization code would still make F1 pull and flag dst=604 calls
as missed-call numbers without properly categorizing them, so it must be
removed too for a clean revert.
"""
from sqlalchemy import text

from storage.db import engine

WEBSITE_HANNAH_AI_NUMBER = "604"


def migrate_remove_website_hannah_ai_sync_filter():
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                DELETE FROM fd_sync_filters
                WHERE filter_type = 'number' AND value = :value
                """
            ),
            {"value": WEBSITE_HANNAH_AI_NUMBER},
        )


if __name__ == "__main__":
    migrate_remove_website_hannah_ai_sync_filter()
