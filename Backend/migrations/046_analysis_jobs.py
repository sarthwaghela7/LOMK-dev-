"""Durable AI responses and cross-process admission control for both apps."""
from sqlalchemy import text
from storage.db import engine


def migrate_analysis_jobs():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS analysis_jobs (
                id UUID PRIMARY KEY,
                call_id UUID NOT NULL REFERENCES calls(call_id) ON DELETE CASCADE,
                application TEXT NOT NULL CHECK (application IN ('autoflint', 'flintdesk')),
                request_key UUID,
                status TEXT NOT NULL CHECK (status IN
                    ('generating', 'response_ready', 'completed', 'review_required')),
                response JSONB,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (call_id, application, request_key)
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS analysis_jobs_one_unfinished
            ON analysis_jobs(call_id, application) WHERE status <> 'completed'
        """))
        # Provider responses contain call data. Expose no direct client policy;
        # the backend's database owner/service role manages this table.
        conn.execute(text("ALTER TABLE analysis_jobs ENABLE ROW LEVEL SECURITY"))
