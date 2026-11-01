"""Cross-agent case closure synchronisation: closure events and the per-record links.

fd_case_closures      one row per closing event (the authoritative closer, status and context)
fd_case_closure_links one row per OTHER agent's log that confirmed the closure; the log's own
                      fd_m_case_status_change is never rewritten, so no closure credit is shared.
"""
from sqlalchemy import text

from storage.db import engine


def migrate_create_case_closures():
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS fd_case_closures (
                id UUID PRIMARY KEY,
                client_id TEXT NOT NULL,
                closing_analysis_id BIGINT NOT NULL,
                closing_call_id UUID,
                closing_user_id UUID,
                closing_agent_name TEXT,
                closure_status TEXT NOT NULL,
                context_key TEXT NOT NULL,
                case_context TEXT,
                closed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                reverted_at TIMESTAMPTZ,
                reverted_by UUID
            )
        """))
        # One active closure per closing record: retries and double submits cannot create a second one.
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS fd_case_closures_one_active_per_record "
            "ON fd_case_closures (closing_analysis_id) WHERE reverted_at IS NULL"
        ))
        connection.execute(text("CREATE INDEX IF NOT EXISTS fd_case_closures_client ON fd_case_closures (client_id)"))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS fd_case_closure_links (
                id UUID PRIMARY KEY,
                closure_id UUID NOT NULL REFERENCES fd_case_closures(id) ON DELETE CASCADE,
                analysis_id BIGINT NOT NULL,
                call_id UUID,
                agent_user_id UUID,
                confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                confirmed_by UUID,
                reverted_at TIMESTAMPTZ,
                CONSTRAINT fd_case_closure_links_closure_analysis_key UNIQUE (closure_id, analysis_id)
            )
        """))
        # A log inherits at most one active closure.
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS fd_case_closure_links_one_active_per_record "
            "ON fd_case_closure_links (analysis_id) WHERE reverted_at IS NULL"
        ))


if __name__ == "__main__":
    migrate_create_case_closures()
