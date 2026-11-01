"""Read-only, bounded incident snapshot. Never imports the app or starts workers."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool


QUERIES = {
    "clock": "SELECT now() AS captured_at, current_setting('TimeZone') AS server_timezone, pg_postmaster_start_time() AS server_started_at, version() AS version",
    "statement_reset": "SELECT * FROM pg_stat_statements_info",
    "database_stats": "SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit, temp_bytes, deadlocks, stats_reset FROM pg_stat_database WHERE datname=current_database()",
    "connections": "SELECT application_name, usename, state, wait_event_type, wait_event, count(*) AS connections, max(now()-xact_start) AS oldest_transaction FROM pg_stat_activity WHERE datname=current_database() GROUP BY 1,2,3,4,5",
    "blocking": "SELECT pid, application_name, state, wait_event_type, wait_event, now()-query_start AS query_age, pg_blocking_pids(pid) AS blockers FROM pg_stat_activity WHERE datname=current_database() AND cardinality(pg_blocking_pids(pid))>0",
    "temp_writers": "SELECT queryid::text, calls, total_exec_time, rows, temp_blks_read, temp_blks_written FROM pg_stat_statements(false) WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database()) AND temp_blks_written>0 ORDER BY temp_blks_written DESC LIMIT 10",
    # Keep private literals/phone numbers out of the artifact. queryid identifies
    # the query in a separately authorized SQL-console investigation.
    # false avoids materializing all normalized query texts: thousands of
    # parameterized UUID variants make even the statistics view spill to disk.
    "top_statements": "SELECT queryid::text, calls, total_exec_time, mean_exec_time, max_exec_time, rows, shared_blks_hit, shared_blks_read, temp_blks_written, total_plan_time, mean_plan_time FROM pg_stat_statements(false) WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database()) ORDER BY total_exec_time DESC LIMIT 40",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    engine = create_engine(os.environ["DATABASE_URL"], poolclass=NullPool,
                           connect_args={"connect_timeout": 10, "application_name": "incident_readonly_capture"})
    result = {"captured_at_utc": datetime.now(timezone.utc).isoformat()}
    for name, sql in QUERIES.items():
        try:
            with engine.connect() as conn, conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text("SET LOCAL statement_timeout='5s'"))
                conn.execute(text("SET LOCAL lock_timeout='1s'"))
                result[name] = [dict(row) for row in conn.execute(text(sql)).mappings()]
        except Exception as exc:
            result[name] = {"error_type": type(exc).__name__, "sqlstate": getattr(getattr(exc, "orig", None), "pgcode", None)}
    engine.dispose()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, default=str, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(destination), "sections": {key: len(value) if isinstance(value, list) else value for key, value in result.items()}}))


if __name__ == "__main__":
    main()
