"""
Database migration: add FlintDesk-specific fields to the shared backend.

This adds the nine AI analysis columns and five manual columns to
call_analysis_results, plus the FlintDesk processing flag on calls.
These are intentionally isolated under the fd_* naming convention to keep
AutoFlint fields untouched.
"""
from sqlalchemy import text

from storage.db import engine


AI_FIELDS = [
    ("fd_a_client_name", "TEXT"),
    ("fd_a_client_type", "TEXT"),
    ("fd_a_lead_source", "TEXT"),
    ("fd_a_email_address", "TEXT"),
    ("fd_a_urgency_flag", "TEXT"),
    ("fd_a_case_type", "TEXT"),
    ("fd_a_case_worker_assigned", "TEXT"),
    ("fd_a_call_outcome", "TEXT"),
    ("fd_a_call_summary", "TEXT"),
]

MANUAL_FIELDS = [
    ("fd_m_case_status_change", "TEXT"),
    ("fd_m_date_entry", "DATE"),
    ("fd_m_verification_comment", "TEXT"),
    ("fd_m_attachment", "TEXT"),
    ("fd_m_google_review", "TEXT"),
]


def migrate_add_flintdesk_fields():
    with engine.begin() as conn:
        # This function is called during every application startup.  PostgreSQL
        # takes an AccessExclusiveLock for ALTER TABLE even with IF NOT EXISTS,
        # so the old single 14-column ALTER blocked normal traffic and caused
        # Supabase/PostgREST schema-cache refreshes to time out.  Read the
        # metadata first and only alter a database that is actually old.
        existing_columns = {
            row[0]
            for row in conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'call_analysis_results'
            """))
        }
        missing_fields = [
            (name, data_type)
            for name, data_type in AI_FIELDS + MANUAL_FIELDS
            if name not in existing_columns
        ]
        for name, data_type in missing_fields:
            conn.execute(text(
                f"ALTER TABLE call_analysis_results ADD COLUMN {name} {data_type}"
            ))
        if missing_fields:
            print("✓ Added missing FlintDesk columns on call_analysis_results")
        else:
            print("✓ FlintDesk columns on call_analysis_results already exist")

        result = conn.execute(text("SELECT 1 FROM information_schema.columns WHERE table_name = 'calls' AND column_name = 'fd_processing_status' LIMIT 1")).fetchone()
        if not result:
            conn.execute(text(
                "ALTER TABLE calls "
                "ADD COLUMN fd_processing_status TEXT NOT NULL DEFAULT 'not processed'"
            ))
            conn.execute(text(
                "ALTER TABLE calls "
                "ADD CONSTRAINT calls_fd_processing_status_check "
                "CHECK (fd_processing_status IN ('processed', 'not processed'))"
            ))
        else:
            print("✓ calls.fd_processing_status already exists")


if __name__ == "__main__":
    migrate_add_flintdesk_fields()
    print("\n✓ Migration complete!")
