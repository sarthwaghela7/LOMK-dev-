"""Manual values for the Call Updates page (WhatsApp calls, initial emails).

One row per (date, agent, metric). Everything else on that page is computed
from existing Calls / Missed Calls / Dashboard data and is never stored.
"""
from sqlalchemy import text
from storage.db import engine


def migrate_create_call_update_entries():
    with engine.begin() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS fd_call_update_entries (
              id UUID PRIMARY KEY,
              entry_date DATE NOT NULL,
              agent_id UUID NOT NULL REFERENCES fd_users(id) ON DELETE CASCADE,
              metric TEXT NOT NULL
                CHECK (metric IN ('wa_answered', 'wa_missed', 'initial_emails')),
              value INTEGER NOT NULL CHECK (value >= 0),
              note TEXT,
              updated_by UUID REFERENCES fd_users(id) ON DELETE SET NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE (entry_date, agent_id, metric)
            )
        """))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_fd_call_update_entries_date ON fd_call_update_entries(entry_date)"))


if __name__ == "__main__":
    migrate_create_call_update_entries()
