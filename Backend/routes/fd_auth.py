from fastapi import APIRouter, HTTPException, Depends, Request
from datetime import datetime, timezone, timedelta
from middleware.auth import get_current_user
import logging
from pydantic import BaseModel
from sqlalchemy import text
# Added DB error handling for FlintDesk login so a DB outage returns 503 instead of a 500.
from sqlalchemy.exc import SQLAlchemyError

from storage.db import engine

from utils.security import hash_password, verify_password
from utils.jwt import create_access_token
from utils.login_throttle import check_login_allowed, clear_login_failures, record_login_failure

router = APIRouter(prefix="/fd-auth", tags=["FlintDesk Auth"])
logger = logging.getLogger(__name__)
_DUMMY_HASH = hash_password("timing-equalisation-only")


class FDLoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def fd_login(payload: FDLoginRequest, request: Request):
    check_login_allowed(request, payload.username)
    # Fix: guard the FlintDesk login query against DB connection failures.
    try:
        with engine.connect() as conn:
            user = conn.execute(
                text(
                    "SELECT id, name, email, pass_hash, is_active, COALESCE(role, 'agent') "
                    "FROM fd_users WHERE email = :email"
                ),
                {"email": payload.username.strip().lower()},
            ).fetchone()
    except SQLAlchemyError as exc:
        # Return a client-friendly 503 response when Postgres is down.
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    if not user:
        # Spend the same bcrypt time as a real check so response timing does
        # not reveal which emails have accounts.
        verify_password(payload.password, _DUMMY_HASH)
        record_login_failure(request, payload.username)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user_id, name, email, pass_hash, is_active, role = user

    if not is_active or not verify_password(payload.password, pass_hash):
        record_login_failure(request, payload.username)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    clear_login_failures(request, payload.username)

    normalized_role = str(role or "agent").strip().lower()
    is_privileged = normalized_role in {"admin", "team_lead", "team lead", "subject_matter_expert", "superuser", "super_user"}
    token = create_access_token({"fd_user_id": str(user_id), "email": email, "name": name, "role": normalized_role,
                                 "session_exp": int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp())})

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": str(user_id),
            "name": name,
            "email": email,
            "role": normalized_role,
            "is_team_lead": is_privileged,
            "is_admin": normalized_role in {"admin", "superuser", "super_user"},
        },
    }


@router.post("/renew")
def renew_fd_session(current_user=Depends(get_current_user)):
    """Renew an active session; recheck account and role, with a fixed 24-hour ceiling."""
    now = int(datetime.now(timezone.utc).timestamp())
    session_exp = current_user.get("session_exp", now + 24 * 3600)
    if not current_user.get("fd_user_id") or session_exp <= now:
        logger.info("FlintDesk renewal rejected: session expired or wrong account type")
        raise HTTPException(status_code=401, detail="Session expired; please sign in again")
    try:
        with engine.connect() as conn:
            user = conn.execute(text(
                "SELECT id, name, email, is_active, COALESCE(role, 'agent') FROM fd_users WHERE id = :id"
            ), {"id": current_user["fd_user_id"]}).fetchone()
    except SQLAlchemyError as exc:
        logger.warning("FlintDesk renewal deferred: database unavailable")
        raise HTTPException(status_code=503, detail="Database unavailable; retry session renewal") from exc
    if not user or not user[3]:
        logger.info("FlintDesk renewal rejected: account inactive or missing")
        raise HTTPException(status_code=401, detail="Account is inactive or unavailable")
    return {"access_token": create_access_token({
        "fd_user_id": str(user[0]), "name": user[1], "email": user[2],
        "role": str(user[4]).strip().lower(), "session_exp": session_exp,
    }), "token_type": "bearer"}
