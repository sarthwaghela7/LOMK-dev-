from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from middleware.auth import get_current_user
from storage.db import get_db
from storage.models import FDTodo

router = APIRouter(prefix="/api/flintdesk/todos", tags=["FlintDesk To-do"])
STATUSES = ("To Do", "In Progress", "Completed", "Blocker", "Dropped")


class TodoCreate(BaseModel):
    title: str
    description: str | None = None
    entry_date: date | None = None
    due_date: date | None = None
    status: str = "To Do"


class TodoUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    entry_date: date | None = None
    due_date: date | None = None
    status: str | None = None


class StatusUpdate(BaseModel):
    direction: Literal["next", "prev"] | None = None
    status: str | None = None


def owner_id(user: dict) -> UUID:
    try:
        return UUID(str(user["fd_user_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(401, "FlintDesk user identity is required") from exc


def own_todo(db: Session, todo_id: UUID, user: dict) -> FDTodo:
    """Fetch a to-do only if it belongs to the caller; 404 otherwise."""
    todo = db.query(FDTodo).filter(FDTodo.id == todo_id, FDTodo.owner_id == owner_id(user)).first()
    if not todo:
        raise HTTPException(404, "To-do card not found")
    return todo


def fields_set(payload: BaseModel) -> set[str]:
    return getattr(payload, "model_fields_set", getattr(payload, "__fields_set__", set()))


def validate_title(value: str | None) -> str:
    title = str(value or "").strip()
    if not title:
        raise HTTPException(422, "Title is required")
    return title


def validate_status(value: str) -> str:
    if value not in STATUSES:
        raise HTTPException(422, f"Status must be one of: {', '.join(STATUSES)}")
    return value


def serialize(todo: FDTodo) -> dict:
    return {
        "id": str(todo.id), "title": todo.title, "description": todo.description,
        "status": todo.status, "entry_date": todo.entry_date.isoformat(),
        "due_date": todo.due_date.isoformat() if todo.due_date else None,
        "created_at": todo.created_at.isoformat(), "updated_at": todo.updated_at.isoformat(),
    }


@router.get("")
def list_todos(status: str | None = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(FDTodo).filter(FDTodo.owner_id == owner_id(user))
    if status is not None:
        query = query.filter(FDTodo.status == validate_status(status))
    return [serialize(todo) for todo in query.order_by(FDTodo.created_at.desc()).all()]


@router.post("", status_code=201)
def create_todo(payload: TodoCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    todo = FDTodo(
        id=uuid4(), owner_id=owner_id(user), title=validate_title(payload.title), description=payload.description or None,
        status=validate_status(payload.status), entry_date=payload.entry_date or now.date(),
        due_date=payload.due_date, created_at=now, updated_at=now,
    )
    db.add(todo); db.commit(); db.refresh(todo)
    return serialize(todo)


@router.patch("/{todo_id}")
def update_todo(todo_id: UUID, payload: TodoUpdate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    todo = own_todo(db, todo_id, user)
    supplied = fields_set(payload)
    if "title" in supplied:
        todo.title = validate_title(payload.title)
    if "status" in supplied:
        todo.status = validate_status(payload.status or "")
    for field in ("description", "entry_date", "due_date"):
        if field in supplied:
            setattr(todo, field, getattr(payload, field))
    if todo.entry_date is None:
        raise HTTPException(422, "Entry date is required")
    todo.updated_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(todo)
    return serialize(todo)


@router.patch("/{todo_id}/status")
def move_todo(todo_id: UUID, payload: StatusUpdate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    todo = own_todo(db, todo_id, user)
    if payload.status is not None:
        target = validate_status(payload.status)
    elif payload.direction:
        index = STATUSES.index(todo.status)
        target = STATUSES[min(index + 1, len(STATUSES) - 1)] if payload.direction == "next" else STATUSES[max(index - 1, 0)]
    else:
        raise HTTPException(422, "Provide direction or status")
    todo.status = target
    todo.updated_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(todo)
    return serialize(todo)


@router.delete("/{todo_id}", status_code=204)
def delete_todo(todo_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    todo = own_todo(db, todo_id, user)
    db.delete(todo); db.commit()
    return Response(status_code=204)
