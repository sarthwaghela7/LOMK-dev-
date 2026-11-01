"""Pending-assignment badges of "Assigning the Calls": how many records each tab still needs the signed-in user to act on.

Only the caller's own counts are ever returned (the owner is always the token's user, there is no user parameter), and each
count reuses the rule of its own tab so the badge matches the rows the tab shows:

  leads      assigned to me (owner saved, import date set) and no outbound call made to the lead's number yet
  follow-ups submitted to me as Retainer Owner (Submit pressed) whose action status is not Completed / Dropped
  sm-leads   assigned to me and the client has not been called on the import day (the tab's derived "Completed")
  general    Call Tasks owned by me (General + Non-Responsive) not yet Called Back / Done / Completed / Resolved / Closed
  missed-calls  Missed Call tasks owned by me with no matching outbound callback
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from middleware.auth import get_current_user
from routes.calls import _system_numbers
from routes.flintdesk import leads as leads_routes
from routes.flintdesk import sm_leads as sm_routes
from routes.flintdesk import tasks as task_routes
from storage.db import get_db
from storage.models import FDLead, FDSMLead

router = APIRouter(prefix="/api/flintdesk/assignment-counts", tags=["FlintDesk Assignment Counts"])

DONE_TASK_VALUES = {"called back", "done", "completed", "resolved", "closed"}
TAB_KEYS = ("leads", "follow-ups", "sm-leads", "general", "missed-calls")


def _me(user: dict) -> UUID:
    try:
        return UUID(str(user.get("fd_user_id") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid session") from exc


def _leads(db: Session, me: UUID) -> int:
    rows = db.query(FDLead).filter(FDLead.fd_m_leads_owner_id == me, FDLead.lead_added_at.isnot(None)).all()
    if not rows:
        return 0
    _, first_after = leads_routes._assigned_outbound_calls_by_lead(db, rows)
    return sum(1 for row in rows if row.id not in first_after)


def _follow_ups(db: Session, me: UUID) -> int:
    return int(db.execute(text("""
        SELECT count(*) FROM fd_follow_up_retainer r
        WHERE r.retainer_owner_id = :me AND r.submitted_at IS NOT NULL
          AND coalesce(nullif(btrim(r.retainer_action_status), ''), 'Pending') NOT IN ('Completed', 'Dropped')
          AND EXISTS (SELECT 1 FROM call_analysis_results a WHERE a.call_id::text = r.call_id::text AND a.fd_m_case_status_change = 'Success PC')
    """), {"me": str(me)}).scalar() or 0)


def _sm_leads(db: Session, me: UUID) -> int:
    expr = sm_routes._call_made(_system_numbers(db))
    rows = db.query(FDSMLead.id, expr.label("call_made")).filter(FDSMLead.owner_id == me, FDSMLead.status != "Pending").all()
    return sum(1 for _, made in rows if not made)


def _tasks(db: Session, me: UUID, user: dict) -> tuple[int, int]:
    """(Call Tasks, Missed Calls) from the Tasks API's own rows for this owner, so each badge equals its tab's pending rows."""
    items = task_routes.get(agent=str(me), include_callback_status=True, user=user, db=db)
    general = missed = 0
    for item in items:
        data = item.get("data") or {}
        kind = item.get("task_type") or data.get("task_type")
        if kind == "missed_call":
            if not data.get("callback_matched") and not data.get("callback"):
                missed += 1
        elif kind in ("general", "non_responsiveness"):
            value = data.get("action_status") if kind == "non_responsiveness" else data.get("status")
            if str(value or "").strip().lower() not in DONE_TASK_VALUES:
                general += 1
    return general, missed


@router.get("")
def my_assignment_counts(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    me = _me(user)
    general, missed = _tasks(db, me, user)
    return {"counts": {"leads": _leads(db, me), "follow-ups": _follow_ups(db, me), "sm-leads": _sm_leads(db, me),
                       "general": general, "missed-calls": missed}}
