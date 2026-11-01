from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from middleware.auth import get_current_user
from routes.flintdesk.leads import _can_manage_leads, _current_fd_user_id
from storage.db import get_db
from storage.models import FDCallUpdateEntry, FDUser
from utils.ttl_cache import cache_get, cache_set

router = APIRouter(prefix="/api/flintdesk/call-updates", tags=["FlintDesk Call Updates"])
logger = logging.getLogger(__name__)
MAX_RANGE_DAYS = 92
MAX_VALUE = 100_000
NOTE_MAX_LENGTH = 1000
# Cleared by the app-wide write hook (invalidate_read_caches) after any change.
CACHE_NAMESPACE = "call_updates"


class ManualEntry(BaseModel):
    entry_date: date
    agent_id: UUID
    metric: Literal["wa_answered", "wa_missed", "initial_emails"]
    value: int = Field(ge=0, le=MAX_VALUE)
    # Only Initial Emails Sent carries a note.
    note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)


def _today_central() -> date:
    from services.stage_completion import case_status_change_date
    return case_status_change_date()


@router.get("")
def get_call_updates(
    start_date: date,
    end_date: date,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if end_date < start_date:
        raise HTTPException(400, "To Date must be on or after From Date.")
    if (end_date - start_date).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(400, f"Date range must be {MAX_RANGE_DAYS} days or fewer.")
    from routes.fd_users import list_front_desk_agents
    from services.call_updates import build_call_updates, manual_entries
    from services.dashboard_summary import invoke_read

    agents = invoke_read(list_front_desk_agents, db, user)
    key = (str(start_date), str(end_date))
    automated = cache_get(CACHE_NAMESPACE, key)
    if automated is None:
        automated = build_call_updates(db, user, start_date, end_date, agents)
        cache_set(CACHE_NAMESPACE, key, automated, ttl_seconds=60)
    return automated


@router.put("/manual")
def save_manual_entry(payload: ManualEntry, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Create or update one agent's manual value for one day.

    Enforced here, not just hidden in the UI: an agent may only save their own
    values; admins, team leads and SMEs may save any front desk agent's values.
    """
    current_id = _current_fd_user_id(user)
    if payload.agent_id != current_id and not _can_manage_leads(user, db):
        raise HTTPException(403, "You can only edit your own entries.")
    # The value always belongs to an active front desk agent (a column of the table).
    agent = db.get(FDUser, payload.agent_id)
    if not agent or not agent.is_active or str(agent.role or "").strip().lower() != "agent":
        raise HTTPException(403, "These values can only be entered for front desk agents.")
    if payload.entry_date > _today_central():
        raise HTTPException(422, "Entries cannot be made for a future date.")
    note = (payload.note or "").strip() or None
    if note and payload.metric != "initial_emails":
        raise HTTPException(422, "Only Initial Emails Sent can have a note.")
    row = db.query(FDCallUpdateEntry).filter(
        FDCallUpdateEntry.entry_date == payload.entry_date,
        FDCallUpdateEntry.agent_id == payload.agent_id,
        FDCallUpdateEntry.metric == payload.metric,
    ).first()
    now = datetime.now(timezone.utc)
    if row is None:
        row = FDCallUpdateEntry(id=uuid4(), entry_date=payload.entry_date, agent_id=payload.agent_id,
                                metric=payload.metric, created_at=now)
        db.add(row)
    row.value, row.note, row.updated_by, row.updated_at = payload.value, note, current_id, now
    db.commit()
    return {
        "entry_date": payload.entry_date.isoformat(), "agent_id": str(payload.agent_id),
        "metric": payload.metric, "value": row.value, "note": row.note or "",
    }
