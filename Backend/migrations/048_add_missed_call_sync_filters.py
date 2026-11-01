"""Add fd_sync_filters rows so F1 pulls calls routed to Hannah AI.

dst = 602 or dst = 2243860601 means the automated attendant (Hannah AI)
answered instead of a human agent -- from a Front Desk Agent's perspective
that is a Missed Call. Adding these as ordinary filter_type='number' rows is
enough for F1's existing _filter_calls() to start keeping these CDRs (see
services/Database_sync/f1.py) -- no F1 code change is needed for the fetch
itself.

target_page='missed_calls' reuses fd_sync_filters' existing column (rather
than adding a new one) to mark these two numbers as belonging only to the
dedicated Missed Calls page: routes/calls.py reads rows tagged this way to
keep calls to/from them off the main Calls page, and
services/missed_call_sync.py reads them to know which ingested Call rows
should get an auto-created "missed_call" task.

Registered in main.py's apply_required_flintdesk_migrations() so every
future deploy/startup keeps this applied; safe to re-run any number of
times (ON CONFLICT DO NOTHING).

Usage:
    python3 migrations/048_add_missed_call_sync_filters.py
"""

import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from storage.db import engine

MISSED_CALL_NUMBERS = ("602", "2243860601")


def migrate_add_missed_call_sync_filters():
    """Idempotently register the Hannah AI numbers as Missed-Calls-only."""
    with engine.begin() as connection:
        for value in MISSED_CALL_NUMBERS:
            connection.execute(
                text(
                    """
                    INSERT INTO fd_sync_filters (id, filter_type, value, parent_number, target_page)
                    VALUES (:id, 'number', :value, NULL, 'missed_calls')
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"id": uuid4(), "value": value},
            )


if __name__ == "__main__":
    migrate_add_missed_call_sync_filters()
