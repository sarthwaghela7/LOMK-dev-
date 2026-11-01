from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import logging

from middleware.auth import get_current_user, require_fd_admin, require_fd_settings_view
from storage.db import get_db
from storage.models import FDAssignment, FDSyncFilter

router = APIRouter(prefix="/flintdesk/sync-filters", tags=["FlintDesk Sync Filters"])
VALID_TYPES = {"number", "extension"}


class SyncFilterCreate(BaseModel):
    filter_type: str
    value: str
    parent_number: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


class SyncFilterUpdate(BaseModel):
    value: str

    model_config = ConfigDict(str_strip_whitespace=True)


def _clean_filter(payload: SyncFilterCreate) -> tuple[str, str, str | None]:
    filter_type = payload.filter_type.lower()
    value = payload.value.strip()
    if filter_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="filter_type must be number or extension")
    if not value or len(value) > 64:
        raise HTTPException(status_code=400, detail="value is required and must be 64 characters or fewer")
    parent_number = payload.parent_number.strip() if payload.parent_number else None
    if filter_type == "extension" and not parent_number:
        raise HTTPException(status_code=400, detail="Extensions must belong to a number")
    if filter_type == "number" and parent_number:
        raise HTTPException(status_code=400, detail="Numbers cannot have a parent number")
    if parent_number and not parent_number.isdigit():
        raise HTTPException(status_code=400, detail="parent_number must contain digits only")
    return filter_type, value, parent_number


@router.get("", dependencies=[Depends(require_fd_settings_view)])
def list_sync_filters(db: Session = Depends(get_db)):
    rows = db.query(FDSyncFilter).order_by(FDSyncFilter.filter_type, FDSyncFilter.parent_number, FDSyncFilter.value).all()
    seen = set()
    result = []
    for row in rows:
        identity = (row.filter_type, row.value, row.parent_number or "")
        if identity in seen:
            continue
        seen.add(identity)
        result.append(
        {
            "id": str(row.id),
            "filter_type": row.filter_type,
            "value": row.value,
            "parent_number": row.parent_number,
            "created_at": row.created_at,
        }
        )
    return result


@router.post("", status_code=201)
def add_sync_filter(payload: SyncFilterCreate, current_user=Depends(require_fd_admin), db: Session = Depends(get_db)):
    filter_type, value, parent_number = _clean_filter(payload)
    if filter_type == "extension":
        parent_exists = db.query(FDSyncFilter).filter(
            FDSyncFilter.filter_type == "number",
            FDSyncFilter.value == parent_number,
        ).first()
        if not parent_exists:
            raise HTTPException(status_code=400, detail="Add the parent number before adding an extension")
    existing = (
        db.query(FDSyncFilter)
        .filter(
            FDSyncFilter.filter_type == filter_type,
            FDSyncFilter.value == value,
            FDSyncFilter.parent_number == parent_number,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="This filter already exists")

    row = FDSyncFilter(
        id=uuid4(),
        filter_type=filter_type,
        value=value,
        parent_number=parent_number,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    # Sync filters are global admin config, not agent-specific data, so this
    # change is not broadcast as a per-agent notification.
    db.commit()
    db.refresh(row)
    return {"id": str(row.id), "filter_type": row.filter_type, "value": row.value, "parent_number": row.parent_number, "created_at": row.created_at}


@router.patch("/{filter_id}")
def update_sync_filter(
    filter_id: UUID,
    payload: SyncFilterUpdate,
    current_user=Depends(require_fd_admin),
    db: Session = Depends(get_db),
):
    """Rename a configured number or extension without recreating its record."""
    row = db.query(FDSyncFilter).filter(FDSyncFilter.id == filter_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Sync filter not found")
    value = payload.value.strip()
    if not value or len(value) > 64:
        raise HTTPException(status_code=400, detail="value is required and must be 64 characters or fewer")
    if row.filter_type == "number" and not value.isdigit():
        raise HTTPException(status_code=400, detail="Sync number must contain digits only")
    if row.filter_type == "extension" and not all(character.isdigit() or character in "+*#-" for character in value):
        raise HTTPException(status_code=400, detail="Extension must contain only digits or valid dialing characters")
    if value == row.value:
        return {"id": str(row.id), "filter_type": row.filter_type, "value": row.value, "parent_number": row.parent_number, "created_at": row.created_at}

    duplicate = db.query(FDSyncFilter).filter(
        FDSyncFilter.filter_type == row.filter_type,
        FDSyncFilter.value == value,
        FDSyncFilter.parent_number == row.parent_number,
        FDSyncFilter.id != row.id,
    ).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="This filter already exists")

    old_value = row.value
    if row.filter_type == "number":
        # Keep child extension configuration and the existing agent assignment
        # pointed at the renamed source number.
        db.query(FDSyncFilter).filter(
            FDSyncFilter.filter_type == "extension",
            FDSyncFilter.parent_number == old_value,
        ).update({FDSyncFilter.parent_number: value}, synchronize_session=False)
        db.query(FDAssignment).filter(FDAssignment.number == old_value).update(
            {FDAssignment.number: value}, synchronize_session=False
        )
    else:
        # Renaming an extension preserves its agent rather than creating or
        # deleting an assignment relationship.
        db.query(FDAssignment).filter(
            FDAssignment.extension == old_value,
            FDAssignment.number == row.parent_number,
        ).update({FDAssignment.extension: value}, synchronize_session=False)
    row.value = value
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(row)
    return {"id": str(row.id), "filter_type": row.filter_type, "value": row.value, "parent_number": row.parent_number, "created_at": row.created_at}


@router.delete("/{filter_id}", status_code=204)
def delete_sync_filter(filter_id: str, current_user=Depends(require_fd_admin), db: Session = Depends(get_db)):
    try:
        parsed_filter_id = UUID(filter_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid sync filter ID") from exc

    row = db.query(FDSyncFilter).filter(FDSyncFilter.id == parsed_filter_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Sync filter not found")
    try:
        if row.filter_type == "number":
            db.query(FDSyncFilter).filter(
                FDSyncFilter.filter_type == "extension",
                FDSyncFilter.parent_number == row.value,
            ).delete(synchronize_session=False)
        db.delete(row)
        # Global configuration changes do not create per-agent notifications.
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logging.getLogger(__name__).warning("Sync filter deletion blocked by dependencies: %s", parsed_filter_id)
        raise HTTPException(status_code=409, detail="This lead number is still referenced by other records.") from exc
