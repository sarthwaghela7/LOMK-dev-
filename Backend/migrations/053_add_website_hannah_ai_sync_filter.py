"""Add the fd_sync_filters row so F1 pulls calls routed to Hannah AI via the
Website ring route (dst = 604).

Mirrors migration 048 exactly, which did this for the Front Desk Agent
route's hand-off leg (dst = 602) plus the shared Hannah AI number
(2243860601, already seeded by 048 and reused as-is here). dst = 604 means
the Website ring route (lead 2243374447 -> route 603 -> extensions 201/202)
went unanswered and handed off to Hannah AI -- from a Front Desk Agent's
perspective that is also a Missed Call. Tagging it target_page='missed_calls'
reuses the exact same fd_sync_filters column 048 relied on, so routes/calls.py
and services/missed_call_sync.py pick it up automatically with no further
code change -- see services/hannah_ai_routing.py for the categorization this
enables (Front Desk Agent vs Website).
"""
from uuid import uuid4

from sqlalchemy import text

from storage.db import engine

WEBSITE_HANNAH_AI_NUMBER = "604"


def migrate_add_website_hannah_ai_sync_filter():
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO fd_sync_filters (id, filter_type, value, parent_number, target_page)
                VALUES (:id, 'number', :value, NULL, 'missed_calls')
                ON CONFLICT DO NOTHING
                """
            ),
            {"id": uuid4(), "value": WEBSITE_HANNAH_AI_NUMBER},
        )


if __name__ == "__main__":
    migrate_add_website_hannah_ai_sync_filter()
