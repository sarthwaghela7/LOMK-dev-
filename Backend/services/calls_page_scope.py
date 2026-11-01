"""Which calls the Calls page (Call Records table) lists.

Stage 2 AI is generated only for these. The Calls page deliberately hides the
automated-attendant routing legs, missed-call numbers and unanswered inbound
legs (they belong to Missed Calls), so those records get no Stage 2 values.
Uses the Calls page's own query (analysis_read, target_page="calls"), so the two
can never disagree.
"""
import logging

from utils.ttl_cache import cache_get, cache_set

logger = logging.getLogger("flintdesk.calls_page_scope")
_NAMESPACE = "calls_page_scope"
_TTL_SECONDS = 120


def calls_page_call_ids(db, day) -> set[str]:
    """Call ids the Calls page lists for one call date (cached briefly)."""
    cached = cache_get(_NAMESPACE, (day.isoformat(),))
    if cached is not None:
        return cached
    from services.analysis_read_query import analysis_read

    shown, _, _ = analysis_read(db, start=day, end=day, call_source=True, target_page="calls")
    ids = {str(call.call_id) for call in shown}
    cache_set(_NAMESPACE, (day.isoformat(),), ids, ttl_seconds=_TTL_SECONDS)
    return ids


def is_on_calls_page(call) -> bool:
    """True when this call is listed in the Call Records table.

    Fails closed: if the check cannot run, Stage 2 is skipped (and logged) rather
    than spent on a record that may not belong there; regenerate/backfill can fill it later.
    """
    from storage.db import SessionLocal

    try:
        with SessionLocal() as db:
            return str(call.call_id) in calls_page_call_ids(db, call.call_date)
    except Exception as exc:  # noqa: BLE001
        logger.error("calls-page membership check failed for %s: %s", getattr(call, "call_id", None), type(exc).__name__)
        return False
