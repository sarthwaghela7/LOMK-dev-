from sqlalchemy import text
from storage.db import engine


def migrate_create_fd_leads():
    with engine.begin() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS fd_leads (
              id UUID PRIMARY KEY, fd_m_leads_lead_id TEXT NOT NULL UNIQUE,
              fd_m_leads_date_received TEXT, fd_m_leads_source TEXT, fd_m_leads_name TEXT,
              fd_m_leads_phone TEXT, fd_m_leads_email TEXT, fd_m_leads_client_type TEXT,
              fd_m_leads_client_profile TEXT, fd_m_leads_client_enquiry TEXT,
              fd_m_leads_lead_category TEXT, fd_m_leads_s1_status TEXT, fd_m_leads_owner TEXT,
              fd_m_leads_extension TEXT, fd_m_leads_sid TEXT, fd_a_leads_call_id TEXT,
              fd_a_leads_agent_name TEXT, fd_a_leads_call_direction TEXT, fd_a_leads_call_duration TEXT,
              fd_a_leads_case_type TEXT, fd_a_leads_case_worker_assigned TEXT, fd_a_leads_urgency_flag TEXT,
              fd_a_leads_redirect_escalation_to TEXT, fd_a_leads_call_outcome TEXT,
              fd_a_leads_call_summary TEXT, fd_a_leads_enquiry_question TEXT, fd_a_leads_s2_status TEXT,
              fd_m_leads_self_action_item TEXT, fd_m_leads_action_due_date TEXT, fd_m_leads_action_status TEXT,
              fd_m_leads_dependency TEXT, fd_m_leads_follow_up_dates TEXT, fd_m_leads_follow_up_attempts TEXT,
              fd_m_leads_blocker_comment TEXT, fd_m_leads_case_status TEXT,
              fd_m_leads_case_closing_date TEXT, fd_m_leads_verification_comment TEXT,
              fd_m_leads_attachment TEXT, match_status TEXT, match_count INTEGER,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_fd_leads_phone ON fd_leads(fd_m_leads_phone)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_fd_leads_extension ON fd_leads(fd_m_leads_extension)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_fd_leads_call_id ON fd_leads(fd_a_leads_call_id)"))
        # Backfill only while upgrading a database that still has the legacy
        # JSON column. Later starts run safely after migration 028 removes it.
        legacy_column_exists = c.execute(text("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'call_analysis_results' AND column_name = 'fd_m_leads')")).scalar()
        if legacy_column_exists:
            # Migration 052 renames the lead table's original audit column
            # in-place. This historical backfill still runs at startup on
            # databases retaining the legacy JSON source, so its INSERT must
            # target whichever canonical timestamp column exists.
            lead_added_column = c.execute(text("""
                SELECT CASE WHEN EXISTS (
                  SELECT 1 FROM information_schema.columns
                  WHERE table_schema = 'public' AND table_name = 'fd_leads'
                    AND column_name = 'lead_added_at'
                ) THEN 'lead_added_at' ELSE 'created_at' END
            """)).scalar_one()
            c.execute(text(f"""
          INSERT INTO fd_leads (id, fd_m_leads_lead_id, fd_m_leads_date_received, fd_m_leads_source,
            fd_m_leads_name, fd_m_leads_phone, fd_m_leads_email, fd_m_leads_client_type,
            fd_m_leads_client_profile, fd_m_leads_client_enquiry, fd_m_leads_lead_category,
            fd_m_leads_s1_status, fd_m_leads_owner, fd_m_leads_extension, fd_m_leads_sid,
            fd_a_leads_call_id, fd_a_leads_agent_name, fd_a_leads_call_direction,
            fd_a_leads_call_duration, fd_a_leads_case_type, fd_a_leads_case_worker_assigned,
            fd_a_leads_urgency_flag, fd_a_leads_redirect_escalation_to, fd_a_leads_call_outcome,
            fd_a_leads_call_summary, fd_a_leads_enquiry_question, fd_a_leads_s2_status,
            fd_m_leads_self_action_item, fd_m_leads_action_due_date, fd_m_leads_action_status,
            fd_m_leads_dependency, fd_m_leads_follow_up_dates, fd_m_leads_follow_up_attempts,
            fd_m_leads_blocker_comment, fd_m_leads_case_status, fd_m_leads_case_closing_date,
            fd_m_leads_verification_comment, fd_m_leads_attachment, match_status, match_count, {lead_added_column})
          SELECT md5(random()::text || clock_timestamp()::text)::uuid,
            p.payload->>'lead_id', p.payload->>'date_received', p.payload->>'source', p.payload->>'name',
            p.payload->>'phone', p.payload->>'email', p.payload->>'client_type', p.payload->>'client_profile',
            p.payload->>'client_enquiry', p.payload->>'lead_category', p.payload->>'s1_status', p.payload->>'owner',
            p.payload->>'extension', p.payload->>'sid',
            CASE WHEN p.payload->>'matched_call_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                 THEN (p.payload->>'matched_call_id')::uuid
                 ELSE NULL END,
            p.payload->>'agent_name',
            p.payload->>'call_direction', p.payload->>'call_duration', p.payload->>'case_type',
            p.payload->>'case_worker_assigned', p.payload->>'urgency_flag', p.payload->>'redirect_escalation_to',
            p.payload->>'call_outcome', p.payload->>'call_summary', p.payload->>'enquiry_question', p.payload->>'s2_status',
            p.payload->>'self_action_item', p.payload->>'action_due_date', p.payload->>'action_status',
            p.payload->>'dependency', p.payload->>'follow_up_dates', p.payload->>'follow_up_attempts',
            p.payload->>'blocker_comment', p.payload->>'case_status', p.payload->>'case_closing_date',
            p.payload->>'verification_comment', p.payload->>'attachment', p.payload->>'match_status',
            NULLIF(p.payload->>'match_count', '')::integer, COALESCE(created_at, now())
          FROM call_analysis_results CROSS JOIN LATERAL (SELECT fd_m_leads AS payload) p
          WHERE fd_m_leads IS NOT NULL AND COALESCE(p.payload->>'lead_id', '') <> ''
          ON CONFLICT (fd_m_leads_lead_id) DO NOTHING
        """))


if __name__ == '__main__':
    migrate_create_fd_leads()
