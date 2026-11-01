"""Supabase database size via a read-only PostgreSQL query."""
from sqlalchemy import text

from ..models import MEASURED, Metric, Window, now_utc, unavailable

GIB = 1024 ** 3


def database_size_gb(conn, window: Window) -> Metric:
    at = now_utc()
    src = "pg_database_size(current_database())"
    try:
        size = conn.execute(text("SELECT pg_database_size(current_database())")).scalar()
    except Exception as exc:  # noqa: BLE001
        return unavailable(window, src, f"query failed: {type(exc).__name__}", at)
    # A point-in-time reading taken NOW, not an end-of-day value for a past date.
    note = "GiB (1024^3); point-in-time at collection, not a historical value"
    return Metric(round(size / GIB, 4), MEASURED, src, at, at, at, note)
