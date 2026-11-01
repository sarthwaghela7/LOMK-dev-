import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, EmailStr, constr
from sqlalchemy import text

from storage.db import engine
from utils.audit_log import log_audit_event
from utils.security import hash_password
from middleware.auth import require_role

router = APIRouter(prefix="/users", tags=["Users"])

ALLOWED_ROLES = {"user", "superuser"}


def _serialize_user(user_row):
    return {
        "id": str(user_row.id),
        "name": user_row.name,
        "email": user_row.email,
        "role": user_row.role,
        "is_superuser": user_row.role == "superuser",
        "mob_no": user_row.mob_no,
        "is_active": user_row.is_active,
        "is_email_verified": user_row.is_email_verified,
        "last_login_at": user_row.last_login_at.isoformat() if user_row.last_login_at else None,
        "when_created": user_row.when_created.isoformat() if user_row.when_created else None,
        "updated_at": user_row.updated_at.isoformat() if user_row.updated_at else None,
    }


def _normalize_role(role: Optional[str]) -> str:
    normalized = (role or "user").strip().lower()
    # Handle both "superuser" and "super_user" formats
    if normalized == "super_user":
        normalized = "superuser"
    if normalized not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail="Role must be either 'user' or 'superuser'.")
    return normalized


class UserCreateRequest(BaseModel):
    name: str
    email: EmailStr
    password: constr(min_length=8)
    role: Optional[str] = "user"
    mob_no: Optional[str] = None
    is_active: Optional[bool] = True


class UserUpdateRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[str] = None
    mob_no: Optional[str] = None
    is_active: Optional[bool] = None


class PasswordResetRequest(BaseModel):
    new_password: constr(min_length=8)


@router.get("/", dependencies=[Depends(require_role("superuser"))])
def list_users():
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, name, email, role, mob_no, is_active, is_email_verified,
                       last_login_at, when_created, updated_at
                FROM users
                ORDER BY when_created DESC NULLS LAST
                """
            )
        ).fetchall()
    return [_serialize_user(row) for row in rows]


@router.post("/", dependencies=[Depends(require_role("superuser"))])
def create_user(payload: UserCreateRequest, current_user=Depends(require_role("superuser"))):
    role = _normalize_role(payload.role)
    user_id = uuid.uuid4()
    hashed_password = hash_password(payload.password)
    now = datetime.utcnow()

    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM users WHERE email = :email"),
            {"email": payload.email},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="A user with this email already exists.")

        conn.execute(
            text(
                """
                INSERT INTO users (id, name, email, pass_hash, role, mob_no, is_active, is_email_verified, when_created, updated_at)
                VALUES (:id, :name, :email, :pass_hash, :role, :mob_no, :is_active, :is_email_verified, :when_created, :updated_at)
                """
            ),
            {
                "id": str(user_id),
                "name": payload.name,
                "email": payload.email,
                "pass_hash": hashed_password,
                "role": role,
                "mob_no": payload.mob_no,
                "is_active": payload.is_active,
                "is_email_verified": True,
                "when_created": now,
                "updated_at": now,
            },
        )

    log_audit_event(
        user_id=current_user["user_id"],
        action="create_user",
        status="success",
        message=f"Created user {payload.email}",
        metadata={"email": payload.email, "role": role},
    )

    return {
        "id": str(user_id),
        "name": payload.name,
        "email": payload.email,
        "role": role,
        "is_superuser": role == "superuser",
    }


@router.put("/{user_id}", dependencies=[Depends(require_role("superuser"))])
def update_user(
    user_id: str = Path(...),
    payload: UserUpdateRequest = None,
    current_user=Depends(require_role("superuser")),
):
    if payload is None or payload.dict(exclude_none=True) == {}:
        raise HTTPException(status_code=400, detail="No update fields provided.")

    update_fields = {}
    if payload.name is not None:
        update_fields["name"] = payload.name
    if payload.email is not None:
        update_fields["email"] = payload.email
    if payload.role is not None:
        update_fields["role"] = _normalize_role(payload.role)
    if payload.mob_no is not None:
        update_fields["mob_no"] = payload.mob_no
    if payload.is_active is not None:
        update_fields["is_active"] = payload.is_active

    if not update_fields:
        raise HTTPException(status_code=400, detail="No valid update fields provided.")

    set_clauses = ", ".join([f"{key} = :{key}" for key in update_fields.keys()]) + ", updated_at = :updated_at"
    update_fields["updated_at"] = datetime.utcnow()
    update_fields["id"] = user_id

    if payload.email is not None:
        with engine.connect() as conn:
            existing = conn.execute(
                text("SELECT 1 FROM users WHERE email = :email AND id <> :id"),
                {"email": payload.email, "id": user_id},
            ).fetchone()
            if existing:
                raise HTTPException(status_code=400, detail="A user with this email already exists.")

    with engine.begin() as conn:
        result = conn.execute(
            text(f"UPDATE users SET {set_clauses} WHERE id = :id"),
            update_fields,
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found.")

    log_audit_event(
        user_id=current_user["user_id"],
        action="update_user",
        status="success",
        message=f"Updated user {user_id}",
        metadata={"updated_fields": list(update_fields.keys())},
    )

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, name, email, role, mob_no, is_active, is_email_verified, last_login_at, when_created, updated_at FROM users WHERE id = :id"
            ),
            {"id": user_id},
        ).fetchone()
    return _serialize_user(row)


@router.delete("/{user_id}", dependencies=[Depends(require_role("superuser"))])
def delete_user(user_id: str = Path(...), current_user=Depends(require_role("superuser"))):
    if user_id == current_user["user_id"]:
        raise HTTPException(status_code=400, detail="Users cannot delete themselves.")

    with engine.begin() as conn:
        result = conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found.")

    log_audit_event(
        user_id=current_user["user_id"],
        action="delete_user",
        status="success",
        message=f"Deleted user {user_id}",
        metadata={"deleted_user_id": user_id},
    )

    return {"message": "User deleted successfully."}


@router.post("/{user_id}/reset-password", dependencies=[Depends(require_role("superuser"))])
def reset_user_password(
    user_id: str = Path(...),
    payload: PasswordResetRequest = None,
    current_user=Depends(require_role("superuser")),
):
    hashed_password = hash_password(payload.new_password)
    now = datetime.utcnow()

    with engine.begin() as conn:
        result = conn.execute(
            text("UPDATE users SET pass_hash = :pass_hash, updated_at = :updated_at WHERE id = :id"),
            {"pass_hash": hashed_password, "updated_at": now, "id": user_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found.")

    log_audit_event(
        user_id=current_user["user_id"],
        action="reset_user_password",
        status="success",
        message=f"Reset password for user {user_id}",
        metadata={"reset_user_id": user_id},
    )

    return {"message": "Password reset successfully."}
