"""Add row-claiming columns so concurrent F3 workers/processes never
double-process the same call via SELECT ... FOR UPDATE SKIP LOCKED."""

from sqlalchemy import text
from storage.db import engine


def migrate_fd_claim_columns():
    with engine.begin() as c:
        c.execute(text("ALTER TABLE calls ADD COLUMN IF NOT EXISTS fd_claimed_at TIMESTAMPTZ"))
        c.execute(text("ALTER TABLE calls ADD COLUMN IF NOT EXISTS fd_claimed_by TEXT"))
        c.execute(text("ALTER TABLE calls ADD COLUMN IF NOT EXISTS stt_claimed_at TIMESTAMPTZ"))
        c.execute(text("ALTER TABLE calls ADD COLUMN IF NOT EXISTS stt_claimed_by TEXT"))
        c.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_calls_fd_claimed_at ON calls(fd_claimed_at)"
        ))
        c.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_calls_stt_claimed_at ON calls(stt_claimed_at)"
        ))
