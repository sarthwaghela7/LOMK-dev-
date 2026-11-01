from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from middleware.auth import get_current_user, require_fd_admin, require_fd_settings_view
from storage.db import get_db
from storage.models import FDAssignment, FDSyncFilter, FDUser
from services.flintdesk_notifications import notify_active_agents

router = APIRouter(prefix="/fd-assignments", tags=["FlintDesk Assignments"])
CENTRAL_TZ = ZoneInfo("America/Chicago")
ROUTING_NUMBER_DISPLAY = {"601": "3128781255", "603": "2243374447", "605": "2243939098"}


class AssignmentCreate(BaseModel):
    extension: str
    agent_id: str


def central_now():
    return datetime.now(timezone.utc).astimezone(CENTRAL_TZ)


def serialize_assignment(row):
    raw_number = str(row.number or "")
    is_number_only_routing_assignment = raw_number in ROUTING_NUMBER_DISPLAY and str(row.extension or "") == raw_number
    data = {
        "id": str(row.id),
        "assignment_date": row.assignment_date.isoformat(),
        # 601/603/605 are internal routing trunks. Present their canonical lead
        # numbers to agents, and do not treat a trunk-only assignment as an
        # assigned extension.
        "extension": None if is_number_only_routing_assignment else row.extension,
        "number": ROUTING_NUMBER_DISPLAY.get(raw_number, raw_number),
        "agent_id": str(row.agent_id),
        "agent_name": row.agent.name,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "is_active": row.is_active,
    }
    return data


def active_assignment_for_user(db: Session, user_id):
    try:
        parsed_user_id = UUID(str(user_id))
    except (ValueError, TypeError):
        return None
    return (
        db.query(FDAssignment)
        .filter(
            FDAssignment.agent_id == parsed_user_id,
            FDAssignment.is_active.is_(True),
        )
        .order_by(FDAssignment.created_at.desc())
        .first()
    )


@router.get("/today", dependencies=[Depends(require_fd_settings_view)])
def list_today_assignments(db: Session = Depends(get_db)):
    rows = (
        db.query(FDAssignment)
        .filter(FDAssignment.is_active.is_(True))
        .order_by(FDAssignment.number, FDAssignment.extension)
        .all()
    )
    latest_by_extension = {}
    for row in rows:
        latest_by_extension.setdefault((row.number, row.extension), row)
    return [serialize_assignment(row) for row in latest_by_extension.values()]


@router.post("", status_code=201)
def assign_extension(payload: AssignmentCreate, current_user=Depends(require_fd_admin), db: Session = Depends(get_db)):
    extension = payload.extension.strip()
    if not extension:
        raise HTTPException(status_code=400, detail="Extension is required")

    try:
        agent_id = UUID(payload.agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid agent ID") from exc

    agent = db.query(FDUser).filter(FDUser.id == agent_id, FDUser.is_active.is_(True)).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Active agent not found")
    role = str(agent.role or "agent").strip().lower().replace(" ", "_")
    if role != "agent":
        raise HTTPException(status_code=400, detail="Only Front Desk agents can be assigned to an extension")

    extension_filter = db.query(FDSyncFilter).filter(
        FDSyncFilter.filter_type == "extension",
        FDSyncFilter.value == extension,
    ).first()
    if not extension_filter or not extension_filter.parent_number:
        raise HTTPException(status_code=400, detail="Extension is not configured under a sync number")

    now = datetime.now(timezone.utc)
    today = central_now().date()
    db.query(FDAssignment).filter(
        FDAssignment.extension == extension,
        FDAssignment.is_active.is_(True),
    ).update({FDAssignment.is_active: False}, synchronize_session=False)
    existing = db.query(FDAssignment).filter(
        FDAssignment.assignment_date == today,
        FDAssignment.extension == extension,
    ).first()
    if existing:
        existing.agent_id = agent_id
        existing.number = extension_filter.parent_number
        existing.expires_at = None
        existing.is_active = True
        existing.created_at = now
        row = existing
    else:
        row = FDAssignment(
            id=uuid4(),
            assignment_date=today,
            extension=extension,
            number=extension_filter.parent_number,
            agent_id=agent_id,
            created_at=now,
            expires_at=None,
            is_active=True,
        )
        db.add(row)

    notify_active_agents(
        db,
        title="Settings updated",
        message=f"Extension {extension} was assigned to {agent.name}.",
        notification_type="settings_changed",
        source_page="settings",
        entity_id=row.id,
        change_context={"action": "assignment_saved", "extension": extension, "agent_id": str(agent.id), "agent_name": agent.name},
    )
    db.commit()
    db.refresh(row)
    return serialize_assignment(row)


@router.delete("/{assignment_id}", status_code=204)
def delete_assignment(assignment_id: str, current_user=Depends(require_fd_admin), db: Session = Depends(get_db)):
    try:
        parsed_id = UUID(assignment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid assignment ID") from exc
    row = db.query(FDAssignment).filter(FDAssignment.id == parsed_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Assignment not found")
    context = {"action": "assignment_deleted", "extension": row.extension, "agent_id": str(row.agent_id)}
    row.is_active = False
    row.expires_at = None
    notify_active_agents(
        db,
        title="Settings updated",
        message=f"The assignment for extension {context['extension']} was removed.",
        notification_type="settings_changed",
        source_page="settings",
        entity_id=parsed_id,
        change_context=context,
    )
    db.commit()


@router.get("/me")
def get_my_assignment(user=Depends(get_current_user), db: Session = Depends(get_db)):
    assignment = active_assignment_for_user(db, user.get("fd_user_id"))
    return serialize_assignment(assignment) if assignment else None
