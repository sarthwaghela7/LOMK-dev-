"""Drop the Leads "Stage 3 - Leads Action Tracking" columns from fd_leads.

The Stage 3 tab (self action item, action due date, action status,
dependency, follow-up dates/attempts, blocker comment, case status, case
closing date, verification comment, attachment) and its "Mark Completed"
(s3_completed/s3_completed_at) workflow were removed from the Leads page.
storage/models.py's FDLead model no longer declares these columns, so the
application already ignores them -- this migration is what actually drops
them from the database.

IMPORTANT -- this is a destructive, irreversible migration:

Any lead that had real Stage 3 progress recorded (an action item, a case
status, a verification comment, an uploaded attachment reference, etc.)
will permanently lose that data the moment this runs. This migration is
intentionally NOT wired into main.py's apply_required_flintdesk_migrations()
and must not be run against a database that might hold real Stage 3 data
without first confirming that data is no longer needed -- back it up first
(e.g. `SELECT id, fd_m_leads_lead_id, fd_m_leads_self_action_item, ... FROM
fd_leads WHERE fd_m_leads_self_action_item IS NOT NULL OR ...`) if there is
any doubt.

Usage (only once you have explicitly decided to proceed):
    python3 migrations/050_drop_leads_stage3_columns.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from storage.db import engine

STAGE3_COLUMNS = (
    "fd_m_leads_self_action_item",
    "fd_m_leads_action_due_date",
    "fd_m_leads_action_status",
    "fd_m_leads_dependency",
    "fd_m_leads_follow_up_dates",
    "fd_m_leads_follow_up_attempts",
    "fd_m_leads_blocker_comment",
    "fd_m_leads_case_status",
    "fd_m_leads_case_closing_date",
    "fd_m_leads_verification_comment",
    "fd_m_leads_attachment",
    "s3_completed",
    "s3_completed_at",
)


def migrate_drop_leads_stage3_columns():
    with engine.begin() as connection:
        for column in STAGE3_COLUMNS:
            connection.execute(text(f'ALTER TABLE fd_leads DROP COLUMN IF EXISTS "{column}"'))


if __name__ == "__main__":
    migrate_drop_leads_stage3_columns()
