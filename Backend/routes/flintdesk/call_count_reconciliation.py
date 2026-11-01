"""Admin-only monitoring API for the daily call-count snapshots.

Reads stored history cheaply; recomputes the live count (one bounded query for
one business date) only when a single date is opened. Same admin dependency as
Settings mutations -- no separate authentication.
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from middleware.auth import require_fd_admin
from services import call_count_snapshot as snapshots

router = APIRouter(prefix="/api/flintdesk/call-count-reconciliation", tags=["FlintDesk Call Count Reconciliation"])
logger = logging.getLogger(__name__)


@router.get("")
def get_history(days: int = Query(45, ge=1, le=180), user=Depends(require_fd_admin)):
    return {"timezone": snapshots.BUSINESS_TZ_NAME, "snapshot_days": snapshots.snapshot_days_setting(),
            "items": snapshots.history(days=days)}


@router.get("/{business_date}")
def get_reconciliation(business_date: date, user=Depends(require_fd_admin)):
    if business_date > snapshots.business_today():
        raise HTTPException(400, "That business date is in the future.")
    return snapshots.reconcile_date(business_date)


@router.get("/{business_date}/affected-calls")
def get_affected_calls(business_date: date, agent: str | None = None, extension: str | None = None,
                       user=Depends(require_fd_admin)):
    if business_date > snapshots.business_today():
        raise HTTPException(400, "That business date is in the future.")
    try:
        return snapshots.affected_calls(business_date, agent=agent, extension=extension)
    except Exception:  # noqa: BLE001
        logger.exception("Affected calls lookup failed business_date=%s", business_date)
        raise HTTPException(500, "Could not compare the snapshot with the current calls.") from None


@router.post("/{business_date}/snapshot")
def retry_snapshot(business_date: date, user=Depends(require_fd_admin)):
    """Retry a missing or failed snapshot. A completed snapshot is never replaced."""
    result = snapshots.take_snapshot(business_date)
    if result["outcome"] == "not_closed":
        raise HTTPException(409, "That business day has not closed yet (closes 5:05 PM Central).")
    return result
