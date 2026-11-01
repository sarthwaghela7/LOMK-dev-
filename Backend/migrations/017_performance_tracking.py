"""Add explicit FD roles, call registration timestamps, and status history."""

from sqlalchemy import text

from storage.db import engine


def migrate_performance_tracking():
    with engine.begin() as conn:
        # Do not issue ALTER TABLE ... IF NOT EXISTS on every application
        # boot: PostgreSQL still requests an AccessExclusiveLock even when
        # the column already exists, which deadlocked Railway startup against
        # concurrent workers. Inspect first; only DDL a genuinely old schema.
        has_role = conn.execute(text("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='fd_users' AND column_name='role')")).scalar()
        if not has_role:
            conn.execute(text("ALTER TABLE fd_users ADD COLUMN role TEXT NOT NULL DEFAULT 'agent'"))
        conn.execute(text("UPDATE fd_users SET role = 'team_lead' WHERE LOWER(TRIM(name)) = 'gokul' AND role = 'agent'"))
        has_registered_at = conn.execute(text("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='calls' AND column_name='registered_at')")).scalar()
        if not has_registered_at:
            conn.execute(text("ALTER TABLE calls ADD COLUMN registered_at TIMESTAMPTZ"))
            conn.execute(text("""
                UPDATE calls SET registered_at = (call_date + call_time) AT TIME ZONE 'America/Chicago'
                WHERE registered_at IS NULL AND call_date IS NOT NULL AND call_time IS NOT NULL
            """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS fd_case_status_history (
                id BIGSERIAL PRIMARY KEY,
                analysis_id BIGINT NOT NULL REFERENCES call_analysis_results(id) ON DELETE CASCADE,
                call_id TEXT NOT NULL,
                previous_status TEXT,
                new_status TEXT NOT NULL,
                changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                changed_by UUID REFERENCES fd_users(id) ON DELETE SET NULL
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fd_case_status_history_call_id ON fd_case_status_history(call_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fd_case_status_history_analysis_id ON fd_case_status_history(analysis_id)"))


if __name__ == "__main__":
    migrate_performance_tracking()
