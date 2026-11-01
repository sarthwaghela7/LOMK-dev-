"""Shared persistence helpers for FlintDesk in-app notifications."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from storage.models import FDTaskNotification, FDUser


REVIEWER_ROLES = {"admin", "team_lead", "subject_matter_expert", "superuser", "super_user"}


def normalized_role(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def active_recipient_ids_for_roles(db: Session, roles: Iterable[str], *, exclude_id: UUID | None = None) -> list[UUID]:
    allowed = {normalized_role(role) for role in roles}
    rows = db.query(FDUser).filter(FDUser.is_active.is_(True)).all()
    return [row.id for row in rows if normalized_role(row.role) in allowed and row.id != exclude_id]


def add_notifications(
    db: Session,
    recipient_ids: Iterable[UUID],
    *,
    title: str,
    message: str,
    notification_type: str,
    source_page: str,
    entity_id: object,
    change_context: dict | None = None,
) -> int:
    unique_ids = list(dict.fromkeys(recipient_ids))
    for recipient_id in unique_ids:
        db.add(FDTaskNotification(
            id=uuid4(),
            recipient_id=recipient_id,
            title=title,
            message=message,
            notification_type=notification_type,
            source_page=source_page,
            entity_id=str(entity_id),
            change_context=change_context or {},
            created_at=datetime.now(timezone.utc),
        ))
    return len(unique_ids)


def notify_active_agents(db: Session, **notification) -> int:
    recipients = active_recipient_ids_for_roles(db, {"agent"}, exclude_id=notification.pop("exclude_id", None))
    return add_notifications(db, recipients, **notification)


def notify_dependency_reviewers(db: Session, **notification) -> int:
    recipients = active_recipient_ids_for_roles(db, REVIEWER_ROLES, exclude_id=notification.pop("exclude_id", None))
    return add_notifications(db, recipients, **notification)
