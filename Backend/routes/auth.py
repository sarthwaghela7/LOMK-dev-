from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
# Added DB error handling import so login and /me can return a graceful 503
# instead of crashing with a 500 when Postgres is unavailable.
from sqlalchemy.exc import SQLAlchemyError

from storage.db import engine

from utils.security import verify_password
from utils.jwt import create_access_token
from utils.audit_log import log_audit_event
from utils.login_throttle import check_login_allowed, clear_login_failures, record_login_failure

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(payload: LoginRequest, background_tasks: BackgroundTasks, request: Request):
    check_login_allowed(request, payload.email)
    # Fix: handle DB outage during login without turning it into a 500 server error.
    try:
        with engine.connect() as conn:
            user = conn.execute(
                text("""
                    SELECT id, name, email, pass_hash, role, mob_no, is_active
                    FROM users
                    WHERE email = :email
                """),
                {"email": payload.email}
            ).fetchone()
    except SQLAlchemyError as exc:
        # Log the DB outage and return a clean 503 for the frontend/client.
        log_audit_event(
            user_id=None,
            action="login",
            status="failed",
            message=f"Login failed: database unavailable for email {payload.email}",
            metadata={"email": payload.email, "reason": "database_unavailable"}
        )
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    if not user:
        # Log failed login attempt
        log_audit_event(
            user_id=None,
            action="login",
            status="failed",
            message=f"Login failed: User not found with email {payload.email}",
            metadata={"email": payload.email, "reason": "user_not_found"}
        )
        record_login_failure(request, payload.email)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user_id, name, email, hashed_password, role, mob_no, is_active = user

    if not verify_password(payload.password, hashed_password):
        # Log failed login attempt
        log_audit_event(
            user_id=str(user_id),
            action="login",
            status="failed",
            message=f"Login failed: Invalid password for user {email}",
            metadata={"email": email, "reason": "invalid_password"}
        )
        record_login_failure(request, payload.email)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if is_active is False:
        record_login_failure(request, payload.email)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    clear_login_failures(request, payload.email)
    token = create_access_token(
        {
            "user_id": str(user_id),
            "email": email,
            "role": role
        }
    )

    # Keep the audit commit off the login response path.
    background_tasks.add_task(
        log_audit_event,
        user_id=str(user_id),
        action="login",
        status="success",
        message=f"User {email} logged in successfully",
        metadata={"email": email, "role": role}
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": str(user_id),
            "name": name,
            "email": email,
            "role": role,
            "mob_no": mob_no,
            "is_active": is_active,
            "is_superuser": role in ("superuser", "super_user")
        }
    }


from fastapi import Depends
from middleware.auth import get_current_user

@router.get("/me")
def me(user=Depends(get_current_user)):
    # Fix: same graceful DB outage handling for the /me profile fetch.
    # This keeps the app from throwing a 500 when the database is unavailable.
    try:
        with engine.connect() as conn:
            db_user = conn.execute(
                text("""
                    SELECT id, name, email, role, mob_no, is_active
                    FROM users
                    WHERE id = :user_id
                """),
                {"user_id": user["user_id"]}
            ).fetchone()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    user_id, name, email, role, mob_no, is_active = db_user

    return {
        "id": str(user_id),
        "name": name,
        "email": email,
        "role": role,
        "mob_no": mob_no,
        "is_active": is_active,
        "is_superuser": role in ("superuser", "super_user")
    }


@router.post("/logout")
def logout(user=Depends(get_current_user)):
    """
    Logout endpoint that logs the logout event.
    Frontend should call this before clearing token.
    """
    user_id = user.get("user_id")
    email = user.get("email")
    
    # Log logout event
    log_audit_event(
        user_id=user_id,
        action="logout",
        status="success",
        message=f"User {email} logged out",
        metadata={"email": email}
    )
    
    return {"message": "Logged out successfully"}
