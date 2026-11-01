import os
import logging
import time

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from utils.config import DATABASE_URL


logger = logging.getLogger("autoflint.database")


def _pool_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read a bounded pool setting without allowing an invalid deploy env to break startup."""
    try:
        return max(minimum, min(int(os.getenv(name, default)), maximum))
    except (TypeError, ValueError):
        return default


# The Railway API, F1/F2/F3 workers, and brief page-load request bursts share
# this engine. A 10-connection pool was exhausted even while Supabase still
# had capacity (19 of 60 connections in use), producing QueuePool timeouts on
# ordinary Calls/Dashboard requests. Keep the default bounded well below the
# database limit while leaving it configurable per deployment.
DB_POOL_SIZE = _pool_setting("DB_POOL_SIZE", 12, 2, 24)
DB_MAX_OVERFLOW = _pool_setting("DB_MAX_OVERFLOW", 6, 0, 12)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    # Supabase's pooler (PgBouncer) can close stale transaction connections;
    # pre_ping catches dead sockets before they are reused. pre_ping alone is
    # the primary defense here, so recycle only needs to be a distant safety
    # net, not the trigger for routine reconnects. This project's Supabase
    # instance is in ap-southeast-2; a full reconnect (TCP+TLS+auth) measured
    # ~1-2s from a typical client/host, on top of ~300-600ms per query round
    # trip -- previously (60s) that reconnect cost was paid on nearly every
    # single Calls-page field save, since agents rarely save more than once
    # a minute. 1800s still recycles periodically without doing that on
    # almost every request.
    pool_recycle=1800,
    # Pool configuration for concurrent users plus background jobs:
    #
    # This single process (web API + scheduler jobs) must share the pool:
    #   - F1 Processor: ~1 connection (every minute, holds briefly)
    #   - F2 Processor: ~1 connection (every minute + 15s, holds briefly)
    #   - F3 Processor: ~1 connection (every minute + 30s, holds briefly)
    #   - Several concurrent web requests during a page load
    #   - Background retry/sync: 1-2 connections
    #   - Safety margin for request spikes
    #
    # Previous config (pool_size=2, max_overflow=0) exhausted during concurrent
    # login/Calls/Dashboard requests while F1/F2/F3 held long-lived connections
    # during expensive external operations (API calls, STT, LLM processing).
    #
    # Default config (pool_size=12, max_overflow=6) provides up to 18
    # connections. It is intentionally below the current Supabase limit of
    # 60, and deployment variables can tune it if that limit changes.
    #
    # Critical: F1/F2/F3 must release connections during external operations
    # (Vitel API, S3, STT, LLM) to prevent holding the whole pool.
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=30,
    # PgBouncer transaction mode is safer with explicit TLS and bounded statement
    # timeouts; this keeps the app on the pooler-compatible connection path.
    connect_args={
        "sslmode": "require",
        "options": "-c statement_timeout=30000",
    },
)


@event.listens_for(engine, "connect")
def _force_read_write_transactions(dbapi_connection, connection_record):
    # The Supabase transaction pooler (port 6543) started handing out sessions
    # with default_transaction_read_only=on, which made every UPDATE fail with
    # ReadOnlySqlTransaction while direct and session-mode connections were
    # writable. psycopg2 then issues "BEGIN READ WRITE" client-side for each
    # transaction, overriding that default without an extra round trip.
    dbapi_connection.set_session(readonly=False)


# Production query visibility without logging values (which can include phone
# numbers, emails, or credentials).  Railway logs only statements that cross
# this threshold, along with the pool state at that instant.  This makes it
# possible to identify the precise query responsible for a slow page or pool
# pressure before changing capacity again.
DB_SLOW_QUERY_MS = _pool_setting("DB_SLOW_QUERY_MS", 750, 100, 60_000)


@event.listens_for(engine, "before_cursor_execute")
def _start_query_timer(conn, cursor, statement, parameters, context, executemany):
    context._autoflint_query_started_at = time.perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _log_slow_query(conn, cursor, statement, parameters, context, executemany):
    started_at = getattr(context, "_autoflint_query_started_at", None)
    if started_at is None:
        return
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    if elapsed_ms < DB_SLOW_QUERY_MS:
        return
    # Collapse whitespace and omit bound parameters to keep logs concise and
    # avoid recording customer data.
    sql = " ".join(str(statement).split())[:800]
    logger.warning(
        "[db] slow_query_ms=%.1f pool=%s sql=%s",
        elapsed_ms,
        engine.pool.status(),
        sql,
    )

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    # Every session here is request-scoped (opened and closed within a
    # single request by get_db below), so there is no risk of a later
    # request seeing data another transaction already changed. Without this,
    # SQLAlchemy's default marks every attribute of every object touched in
    # the transaction "expired" on commit, so simply reading a value back
    # in the same request/function (e.g. to build the response) after
    # commit() silently re-runs a full SELECT for that row -- measured as
    # two entirely unnecessary round trips (each ~300-600ms against this
    # project's Supabase instance) on every Calls-page field save.
    expire_on_commit=False,
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
