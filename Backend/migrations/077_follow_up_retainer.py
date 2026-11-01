"""Follow Ups: the Team Lead Fills data of a Success PC call (additive and idempotent).

fd_follow_up_retainer holds at most one row per call (call_id is the primary key and references calls), created the first
time a Team Lead / SME / Admin fills a field. The columns are exactly the eight Team Lead Fills columns of the Follow Ups tab plus
the assignment state written by its Submit button:

    business_status, paid_amount, retainer_follow_up_required, retainer_owner_id, retainer_action_plan,
    retainer_action_status, retainer_status, retainer_verification
    assigned_at / submitted_at / submitted_by   set by Submit; the owner (an agent) is notified then

Allowed values are enforced here as CHECK constraints and again by services/follow_ups.py. No data is rewritten.
Rollback (manual, nothing else depends on it):  DROP TABLE fd_follow_up_retainer;
"""
from sqlalchemy import text

from storage.db import engine

STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS fd_follow_up_retainer (
        call_id UUID PRIMARY KEY REFERENCES calls(call_id) ON DELETE CASCADE,
        business_status TEXT CHECK (business_status IS NULL OR business_status IN ('PC Only', 'PC + Retainer')),
        paid_amount NUMERIC(12, 2) CHECK (paid_amount IS NULL OR paid_amount >= 0),
        retainer_follow_up_required TEXT CHECK (retainer_follow_up_required IS NULL OR retainer_follow_up_required IN ('Yes', 'No')),
        retainer_owner_id UUID REFERENCES fd_users(id) ON DELETE SET NULL,
        retainer_action_plan TEXT,
        retainer_action_status TEXT CHECK (retainer_action_status IS NULL OR retainer_action_status IN ('Pending', 'Completed', 'Dropped')),
        retainer_status TEXT CHECK (retainer_status IS NULL OR retainer_status IN ('Success', 'Lost')),
        retainer_verification TEXT,
        assigned_at TIMESTAMPTZ,
        submitted_at TIMESTAMPTZ,
        submitted_by UUID REFERENCES fd_users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_follow_up_retainer_owner ON fd_follow_up_retainer (retainer_owner_id) WHERE submitted_at IS NOT NULL",
)


def migrate_follow_up_retainer():
    with engine.begin() as connection:
        for statement in STATEMENTS:
            connection.execute(text(statement))
