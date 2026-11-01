from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, constr
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from middleware.auth import get_current_user, require_fd_admin, require_fd_settings_view
from storage.db import get_db
from storage.models import FDAssignment, FDUser
from utils.security import hash_password
from services.flintdesk_notifications import notify_active_agents

router = APIRouter(prefix="/fd-users", tags=["FlintDesk Users"])


class FDUserCreate(BaseModel):
    name: str
    email: EmailStr
    password: constr(min_length=8)
    role: str = "agent"


class FDUserUpdate(BaseModel):
    name: str
    email: EmailStr
    # An omitted or blank password deliberately preserves the existing hash.
    password: constr(min_length=8) | None = None


VALID_FD_ROLES = {"admin", "agent", "team_lead", "subject_matter_expert"}
def serialize_fd_user(user: FDUser):
    return {
        "id": str(user.id),
        "name": user.name,
        "email": user.email,
        "is_active": user.is_active,
        "role": user.role or "agent",
        "created_at": user.created_at,
    }


@router.get("", dependencies=[Depends(require_fd_settings_view)])
def list_fd_users(db: Session = Depends(get_db)):
    users = db.query(FDUser).filter(FDUser.is_active.is_(True), func.lower(func.trim(FDUser.name)) != "manaswi").order_by(FDUser.created_at.desc(), FDUser.name).all()
    return [serialize_fd_user(user) for user in users]


@router.get("/front-desk-agents")
def list_front_desk_agents(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Return the minimal directory needed by the shared Dashboard ranking.

    This is deliberately separate from the Settings user-management endpoint:
    every authenticated FlintDesk user may view the team ranking, while only
    privileged roles may view account-management data such as email addresses.
    """
    agents = (
        db.query(FDUser)
        .filter(
            FDUser.is_active.is_(True),
            func.lower(func.trim(FDUser.role)) == "agent",
        )
        .order_by(FDUser.name)
        .all()
    )
    assignments_by_agent: dict[str, list[str]] = {}
    for assignment in db.query(FDAssignment).filter(FDAssignment.is_active.is_(True)).all():
        agent_id = str(assignment.agent_id)
        extension = str(assignment.extension or "").strip()
        if extension:
            assignments_by_agent.setdefault(agent_id, []).append(extension)
    return [
        {
            "id": str(agent.id),
            "name": agent.name,
            # Match the current Settings assignment source of truth so the
            # Dashboard can display an assigned extension even without calls
            # in the selected reporting range.
            "extensions": sorted(set(assignments_by_agent.get(str(agent.id), []))),
        }
        for agent in agents
    ]


@router.post("", status_code=201)
def create_fd_user(payload: FDUserCreate, current_user=Depends(require_fd_admin), db: Session = Depends(get_db)):
    name = payload.name.strip()
    email = payload.email.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    role = payload.role.strip().lower()
    if role not in VALID_FD_ROLES or role == "admin":
        # Admin creation remains a controlled deployment operation, while this
        # Settings API can create only agent, Team Lead, and SME accounts.
        raise HTTPException(status_code=400, detail="role must be agent, team_lead, or subject_matter_expert")
    existing = db.query(FDUser).filter(FDUser.email == email).first()
    if existing:
        raise HTTPException(status_code=409, detail="A FlintDesk user with this email already exists")

    user = FDUser(
        id=uuid4(),
        name=name,
        email=email,
        pass_hash=hash_password(payload.password),
        is_active=True,
        role=role,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    notify_active_agents(
        db,
        title="Settings updated",
        message=f"A {role.replace('_', ' ')} account was added for {name}.",
        notification_type="settings_changed",
        source_page="settings",
        entity_id=user.id,
        change_context={"action": "user_created", "name": name, "role": role},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A FlintDesk user with this email already exists") from exc
    db.refresh(user)
    return serialize_fd_user(user)


@router.patch("/{user_id}")
def update_fd_user(
    user_id: str,
    payload: FDUserUpdate,
    current_user=Depends(require_fd_admin),
    db: Session = Depends(get_db),
):
    """Admin-only edit of account details; role and ID are intentionally immutable."""
    try:
        parsed_user_id = UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid FlintDesk user ID") from exc
    user = db.query(FDUser).filter(FDUser.id == parsed_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="FlintDesk user not found")

    name = payload.name.strip()
    email = payload.email.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    duplicate = db.query(FDUser).filter(FDUser.email == email, FDUser.id != parsed_user_id).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="A FlintDesk user with this email already exists")

    user.name = name
    user.email = email
    if payload.password:
        # Reuse the only password hashing mechanism used by fd-auth.
        user.pass_hash = hash_password(payload.password)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A FlintDesk user with this email already exists") from exc
    db.refresh(user)
    return serialize_fd_user(user)


@router.delete("/{user_id}", status_code=204)
def delete_fd_user(user_id: str, current_user=Depends(require_fd_admin), db: Session = Depends(get_db)):
    try:
        parsed_user_id = UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid FlintDesk user ID") from exc

    user = db.query(FDUser).filter(FDUser.id == parsed_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="FlintDesk user not found")
    removed_name = user.name
    removed_role = user.role
    notice = dict(
        exclude_id=parsed_user_id,
        title="Settings updated",
        message=f"The {str(removed_role or 'agent').replace('_', ' ')} account for {removed_name} was removed.",
        notification_type="settings_changed",
        source_page="settings",
        entity_id=parsed_user_id,
        change_context={"action": "user_deleted", "name": removed_name, "role": removed_role},
    )
    try:
        db.delete(user)
        db.flush()
        notify_active_agents(db, **dict(notice))
        db.commit()
    except IntegrityError:
        # The account is still referenced by calls/leads/tasks history that has
        # no ON DELETE rule. Keep that history intact and retire the account:
        # inactive users disappear from Settings, rankings and cannot sign in.
        db.rollback()
        user = db.query(FDUser).filter(FDUser.id == parsed_user_id).first()
        if not user:
            return
        user.is_active = False
        try:
            notify_active_agents(db, **dict(notice))
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail="Unable to remove this user. Please try again.") from exc
