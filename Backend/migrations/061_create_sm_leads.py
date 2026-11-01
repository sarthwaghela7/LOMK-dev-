"""Create the SM Leads table.

Mirrors the call-numbering scheme from migration 001: a dedicated sequence
supplies the number and the public ``SM#N`` identifier is derived from it, so
IDs are unique, system-generated and never taken from an imported file.
"""
from sqlalchemy import text
from storage.db import engine


def migrate_create_sm_leads():
    with engine.begin() as c:
        c.execute(text("CREATE SEQUENCE IF NOT EXISTS sm_lead_number_seq START 1 INCREMENT 1"))
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS fd_sm_leads (
              id UUID PRIMARY KEY,
              sm_lead_number BIGINT NOT NULL UNIQUE DEFAULT nextval('sm_lead_number_seq'),
              sm_lead_id TEXT GENERATED ALWAYS AS ('SM#' || sm_lead_number::text) STORED,
              date_added TIMESTAMPTZ NOT NULL DEFAULT now(),
              date_received DATE,
              name TEXT,
              email TEXT,
              phone TEXT,
              phone_digits TEXT,
              enquiry_question TEXT,
              owner_id UUID REFERENCES fd_users(id),
              owner TEXT,
              status TEXT NOT NULL DEFAULT 'Pending'
                CHECK (status IN ('Pending', 'Assigned', 'Completed')),
              submitted_at TIMESTAMPTZ,
              completed_at TIMESTAMPTZ,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        c.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_fd_sm_leads_sm_lead_id ON fd_sm_leads(sm_lead_id)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_fd_sm_leads_date_added ON fd_sm_leads(date_added DESC)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_fd_sm_leads_status ON fd_sm_leads(status)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_fd_sm_leads_owner_id ON fd_sm_leads(owner_id)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_fd_sm_leads_phone_digits ON fd_sm_leads(phone_digits)"))


if __name__ == "__main__":
    migrate_create_sm_leads()
