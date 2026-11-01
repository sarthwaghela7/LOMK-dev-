"""Follow Ups API: the Success PC calls of "Assigning the Calls > Follow Ups" and their Team Lead Fills data.

Reading is open to every signed-in user, who all see the same calls (agents view only). Saving fields and Submit are
Admin / Team Lead / Subject Matter Expert only, checked here and not only in the UI. All rules live in services/follow_ups.py.
"""
from datetime import date
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from middleware.auth import get_current_user, require_fd_visa_update_editor
from routes.calls import _reject_oversized_date_range
from services import follow_ups
from storage.db import get_db

router = APIRouter(prefix="/flintdesk/follow-ups", tags=["FlintDesk Follow Ups"])


@router.get("")
def list_follow_ups(
    start_date: date = Query(...),
    end_date: date = Query(...),
    agent: Optional[str] = Query("all"),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _reject_oversized_date_range(start_date, end_date)
    return {"items": follow_ups.list_follow_ups(db, user, start_date, end_date, agent or "all"), "can_edit": follow_ups.is_editor(user)}


@router.patch("/{call_id}")
def save_follow_up_fields(call_id: UUID, updates: Dict[str, Any] = Body(...), user: dict = Depends(require_fd_visa_update_editor), db: Session = Depends(get_db)):
    return follow_ups.save_fields(db, call_id, updates)


@router.post("/{call_id}/submit")
def submit_follow_up(call_id: UUID, user: dict = Depends(require_fd_visa_update_editor), db: Session = Depends(get_db)):
    return follow_ups.submit(db, call_id, user)
