"""Persistent case-closure notifications: list my pending ones, Confirm or dismiss (X).

A user can only ever see and act on their OWN notifications (the recipient id comes from the login token,
never from the request).
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from middleware.auth import get_current_user
from routes.flintdesk.tasks import current_fd_user_id
from services import case_closure
from storage.db import get_db

router = APIRouter(prefix="/flintdesk/case-closures", tags=["FlintDesk Case Closures"])


@router.get("/notifications")
def my_pending_closure_notifications(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return case_closure.pending_notifications(db, current_fd_user_id(user))


@router.post("/notifications/{notification_id}/confirm")
def confirm_closure(notification_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    result = case_closure.confirm_notification(db, notification_id, current_fd_user_id(user))
    if result["state"] == "not_found":
        db.rollback()
        raise HTTPException(404, "Notification not found")
    db.commit()
    return result


@router.post("/notifications/{notification_id}/dismiss")
def dismiss_closure(notification_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    result = case_closure.dismiss_notification(db, notification_id, current_fd_user_id(user))
    if result["state"] == "not_found":
        db.rollback()
        raise HTTPException(404, "Notification not found")
    db.commit()
    return result


class OwnRelatedConfirm(BaseModel):
    # No upper bound on how many related logs there can be; only the ids the agent was shown.
    analysis_ids: list[int] = Field(min_length=1)


@router.get("/own-related")
def my_related_logs_for_closed_call(call_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """After the signed-in agent closes `call_id`: their OWN other open logs for the same client and case context."""
    return case_closure.own_related_offer(db, call_id, current_fd_user_id(user))


@router.post("/{closure_id}/own-related/confirm")
def close_my_related_logs(closure_id: UUID, payload: OwnRelatedConfirm, user=Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """Close the listed related logs with the closure's status. Each is re-validated; stale ones are skipped."""
    result = case_closure.confirm_own_related(db, closure_id, current_fd_user_id(user), payload.analysis_ids)
    if result["state"] == "not_found":
        db.rollback()
        raise HTTPException(404, "Closure not found")
    db.commit()
    return result
