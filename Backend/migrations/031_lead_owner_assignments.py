"""Use canonical FlintDesk users for lead ownership and notifications."""

from sqlalchemy import text

from storage.db import engine


def migrate_lead_owner_assignments():
    with engine.begin() as connection:
        connection.execute(text("""
            ALTER TABLE fd_leads
            ADD COLUMN IF NOT EXISTS fd_m_leads_owner_id UUID REFERENCES fd_users(id)
        """))
        connection.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_fd_leads_owner_id
            ON fd_leads (fd_m_leads_owner_id)
        """))
        # Backfill only exact active Agent name matches. This is a safe bridge
        # for rows created before canonical owner ids existed; it cannot assign
        # an ambiguous or inactive account.
        connection.execute(text("""
            UPDATE fd_leads lead
            SET fd_m_leads_owner_id = agent.id
            FROM fd_users agent
            WHERE lead.fd_m_leads_owner_id IS NULL
              AND lower(trim(lead.fd_m_leads_owner)) = lower(trim(agent.name))
              AND agent.is_active = TRUE
              AND lower(trim(agent.role)) = 'agent'
              AND 1 = (
                SELECT count(*) FROM fd_users candidate
                WHERE candidate.is_active = TRUE
                  AND lower(trim(candidate.role)) = 'agent'
                  AND lower(trim(candidate.name)) = lower(trim(lead.fd_m_leads_owner))
              )
        """))
        connection.execute(text("""
            ALTER TABLE task_notifications
            ALTER COLUMN task_id DROP NOT NULL
        """))
        connection.execute(text("""
            ALTER TABLE task_notifications
            ADD COLUMN IF NOT EXISTS lead_id UUID REFERENCES fd_leads(id)
        """))


if __name__ == '__main__':
    migrate_lead_owner_assignments()
