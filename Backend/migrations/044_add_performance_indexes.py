"""Add indexes on frequently-filtered columns for Calls/Dashboard/Tasks queries."""

from sqlalchemy import text

from storage.db import engine


def migrate_add_performance_indexes():
    with engine.begin() as connection:
        # The shared connection's global statement_timeout (30s) is too
        # short here: CREATE INDEX must wait for any in-flight write on the
        # target table (F1/F2/F3 sync jobs write to `calls` continuously),
        # not because the index build itself is slow. Extend it for this
        # transaction only.
        connection.execute(text("SET LOCAL statement_timeout = '120000'"))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_calls_extension ON calls(extension)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_calls_call_date_call_time ON calls(call_date DESC, call_time DESC)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_call_analysis_results_recording_date_normalized "
            "ON call_analysis_results(call_recording_date_normalized)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_fd_leads_call_id ON fd_leads(fd_a_leads_call_id)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_tasks_owner ON tasks(fd_m_tasks_owner)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_tasks_dates "
            "ON tasks(fd_m_tasks_date, fd_m_tasks_date_added, fd_m_tasks_missed_call_date)"
        ))
