"""SM Lead completion is now derived (an outbound call to the client after
assignment), not set by hand. Turn any manually-set Completed back into
Assigned so the stored status only ever holds Pending or Assigned."""
from sqlalchemy import text
from storage.db import engine


def migrate_sm_leads_derived_completion():
    with engine.begin() as c:
        c.execute(text("""
            UPDATE fd_sm_leads
            SET status = 'Assigned', completed_at = NULL, updated_at = now()
            WHERE status = 'Completed'
        """))


if __name__ == "__main__":
    migrate_sm_leads_derived_completion()
