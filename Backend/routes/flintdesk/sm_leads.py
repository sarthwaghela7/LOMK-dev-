from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Date, and_, case, cast, func, literal, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from middleware.auth import get_current_user
from storage.db import get_db
from storage.models import Call, FDSMLead, FDTaskNotification, FDUser
from services.read_scope import digits, normalized_number
# Role checks, owner resolution and date parsing are shared with Leads so SM
# Leads cannot drift from the established assignment rules.
from routes.calls import _system_numbers
from routes.flintdesk.leads import (
    _can_manage_leads,
    _current_fd_user_id,
    _lead_received_date,
    _owner_agent,
    _require_manager,
)

router = APIRouter(prefix="/api/flintdesk/sm-leads", tags=["FlintDesk SM Leads"])
logger = logging.getLogger(__name__)
CENTRAL_TIMEZONE = ZoneInfo("America/Chicago")
STATUSES = ("Pending", "Assigned", "Completed")
MAX_DATE_RANGE_DAYS = 366


class SMLeadImport(BaseModel):
    records: list[dict[str, Any]] = Field(min_length=1, max_length=5000)


class SMLeadSubmit(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=500)
    # Owner choices travel only with Submit: selecting an owner in the table
    # is a local draft until this request succeeds (same as Leads S1).
    owner_ids: dict[UUID, UUID] = Field(default_factory=dict)


class SMLeadIds(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=500)


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", _text(value))


def _call_made(system_numbers):
    """SQL: an outbound call to this lead's client number was made on the
    same (Central) day the lead was imported (Date of Added). Same outbound
    rule as the Calls page: the call's source is one of FlintDesk's own
    numbers. Only assigned leads can be Completed."""
    added_on = cast(func.timezone("America/Chicago", FDSMLead.date_added), Date)
    outbound_call = select(literal(1)).where(
        func.right(digits(Call.dst), 10) == func.right(FDSMLead.phone_digits, 10),
        Call.call_date == added_on,
        normalized_number(Call.src).in_(list(system_numbers) or [""]),
    ).correlate(FDSMLead).exists()
    return and_(
        FDSMLead.status != "Pending",
        func.coalesce(FDSMLead.phone_digits, "") != "", outbound_call,
    )


def _serialize(row: FDSMLead, call_made: bool = False) -> dict:
    return {
        "id": str(row.id),
        "sm_lead_id": row.sm_lead_id,
        "date_added": row.date_added.isoformat() if row.date_added else None,
        "date_received": row.date_received.isoformat() if row.date_received else None,
        "name": row.name,
        "email": row.email,
        "phone": row.phone,
        "enquiry_question": row.enquiry_question,
        "owner": row.owner,
        "owner_id": str(row.owner_id) if row.owner_id else None,
        # Pending: no owner. Assigned: owner saved via Submit. Completed:
        # assigned and the client was called the day the lead was imported (derived, not stored).
        "status": "Completed" if call_made else ("Assigned" if row.status == "Completed" else row.status),
        "call_made": bool(call_made),
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
    }


def _serialize_ids(db: Session, ids) -> list[dict]:
    expr = _call_made(_system_numbers(db))
    rows = db.query(FDSMLead, expr.label("call_made")).filter(FDSMLead.id.in_(ids)).all()
    return [_serialize(row, made) for row, made in rows]


@router.get("/call-links")
def sm_lead_call_links(
    start_date: date,
    end_date: date,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The call that turned each SM Lead green: its latest outbound call to the
    client's number on the day the lead was imported. Used by the Calls page
    to label those calls "SM_Lead" (like the Leads / Missed Call labels)."""
    if end_date < start_date or (end_date - start_date).days > MAX_DATE_RANGE_DAYS:
        raise HTTPException(400, f"Date range must be ordered and {MAX_DATE_RANGE_DAYS} days or fewer")
    added_on = cast(func.timezone("America/Chicago", FDSMLead.date_added), Date)
    system = list(_system_numbers(db)) or [""]
    matches = (
        db.query(FDSMLead.id, FDSMLead.sm_lead_id, Call.call_id, Call.call_time)
        .join(Call, and_(
            func.right(digits(Call.dst), 10) == func.right(FDSMLead.phone_digits, 10),
            Call.call_date == added_on,
        ))
        .filter(
            FDSMLead.status != "Pending", func.coalesce(FDSMLead.phone_digits, "") != "",
            normalized_number(Call.src).in_(system),
            Call.call_date.between(start_date, end_date),
        ).all()
    )
    latest: dict = {}
    for lead_id, sm_lead_id, call_id, call_time in matches:
        current = latest.get(lead_id)
        if current is None or (call_time or datetime.min.time()) > (current[2] or datetime.min.time()):
            latest[lead_id] = (sm_lead_id, call_id, call_time)
    return [{"id": str(lead_id), "sm_lead_id": sm_lead_id, "call_id": str(call_id)}
            for lead_id, (sm_lead_id, call_id, _time) in latest.items()]


@router.get("")
def list_sm_leads(
    start_date: date | None = None,
    end_date: date | None = None,
    status: str = "",
    phone: str = "",
    sm_lead_id: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    owner_id: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if start_date and end_date and (end_date < start_date or (end_date - start_date).days > MAX_DATE_RANGE_DAYS):
        raise HTTPException(400, f"Date range must be ordered and {MAX_DATE_RANGE_DAYS} days or fewer")
    status = _text(status)
    if status and status not in STATUSES:
        raise HTTPException(400, "Status must be Pending, Assigned or Completed.")

    call_made = _call_made(_system_numbers(db))
    query = db.query(FDSMLead, call_made.label("call_made"))
    # Every signed-in user sees every SM Lead (view-only for agents: import, submit and delete stay manager-only).

    # The shared Agent filter of Assigning the Calls: only the SM Leads owned by that agent. (An agent already sees only their own.)
    if _text(owner_id):
        try:
            query = query.filter(FDSMLead.owner_id == UUID(_text(owner_id)))
        except ValueError:
            raise HTTPException(400, "owner_id must be an agent id.")

    added_on = cast(func.timezone("America/Chicago", FDSMLead.date_added), Date)
    if start_date:
        query = query.filter(added_on >= start_date)
    if end_date:
        query = query.filter(added_on <= end_date)
    if status == "Completed":
        query = query.filter(call_made)
    elif status:
        # A stored "Completed" is a legacy manual value (see migration 062).
        stored = ("Assigned", "Completed") if status == "Assigned" else (status,)
        query = query.filter(FDSMLead.status.in_(stored), ~call_made)
    # Same rule as Leads' client-number filter: compare the last 10 digits so
    # "+1 (312) 878-1255", "3128781255" and a partial "878-1255" all match.
    phone_digits = _digits(phone)[-10:]
    if phone_digits:
        query = query.filter(FDSMLead.phone_digits.contains(phone_digits, autoescape=True))
    if _text(sm_lead_id):
        # Accept "SM#12", "sm 12" or a bare "12".
        wanted = re.sub(r"[^a-z0-9]", "", _text(sm_lead_id).lower())
        if wanted:
            query = query.filter(func.regexp_replace(func.lower(FDSMLead.sm_lead_id), "[^a-z0-9]", "", "g").contains(wanted, autoescape=True))

    total = query.count()
    rows = (
        query.order_by(FDSMLead.date_added.desc(), FDSMLead.sm_lead_number.desc())
        .offset((page - 1) * page_size).limit(page_size).all()
    )
    return {"items": [_serialize(row, made) for row, made in rows], "total": total, "page": page, "page_size": page_size}


@router.post("/import", status_code=201)
def import_sm_leads(payload: SMLeadImport, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Insert already header-mapped records.

    ``date_added`` and ``sm_lead_id`` are generated by the database; any such
    keys in the payload are ignored.
    """
    _require_manager(user, db)
    errors: list[dict] = []
    new_rows: list[FDSMLead] = []
    seen: set[tuple] = set()
    skipped = 0
    for index, source in enumerate(payload.records, 1):
        name, email = _text(source.get("name")), _text(source.get("email"))
        phone, question = _text(source.get("phone")), _text(source.get("enquiry_question"))
        raw_received = _text(source.get("date_received"))
        problems = []
        if not raw_received:
            problems.append("Date Received is required")
        if not name:
            problems.append("Name is required")
        if not email:
            problems.append("Email is required")
        if not _digits(phone):
            problems.append("Phone Number is required")
        received = _lead_received_date(raw_received) if raw_received else None
        if raw_received and not received:
            problems.append("Invalid Date Received. Use MM/DD/YYYY or YYYY-MM-DD")
        if problems:
            errors.append({"row": index, "error": "; ".join(problems) + "."})
            continue
        # Re-importing the same file must not duplicate leads.
        fingerprint = (received, name.lower(), email.lower(), _digits(phone), question.lower())
        duplicate = fingerprint in seen or db.query(FDSMLead.id).filter(
            FDSMLead.date_received == received,
            FDSMLead.phone_digits == _digits(phone),
            func.lower(func.coalesce(FDSMLead.email, "")) == email.lower(),
            func.lower(func.coalesce(FDSMLead.enquiry_question, "")) == question.lower(),
        ).first() is not None
        if duplicate:
            skipped += 1
            continue
        seen.add(fingerprint)
        new_rows.append(FDSMLead(
            id=uuid4(), date_received=received, name=name, email=email, phone=phone,
            phone_digits=_digits(phone), enquiry_question=question or None, status="Pending",
        ))
    if errors:
        raise HTTPException(status_code=422, detail={"message": "Import validation failed; no records were inserted.", "errors": errors})
    try:
        db.add_all(new_rows)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("SM Lead import database commit failed")
        raise HTTPException(status_code=503, detail="SM Leads could not be saved. Please retry shortly.") from exc
    return {"inserted": len(new_rows), "skipped_duplicates": skipped}


@router.post("/submit")
def submit_sm_leads(payload: SMLeadSubmit, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Persist the owner chosen for each row, mark it Assigned and notify the owner."""
    _require_manager(user, db)
    rows = db.query(FDSMLead).filter(FDSMLead.id.in_(payload.ids)).all()
    if len(rows) != len(set(payload.ids)):
        raise HTTPException(status_code=404, detail="One or more selected SM Leads were not found.")
    expr = _call_made(_system_numbers(db))
    completed = {row.sm_lead_id for row, made in
                 db.query(FDSMLead, expr).filter(FDSMLead.id.in_(payload.ids)).all() if made}
    if completed:
        raise HTTPException(status_code=409, detail=f"Already completed (client called): {', '.join(sorted(completed))}.")
    now = datetime.now(timezone.utc)
    owners: dict[UUID, FDUser] = {}
    resolved = []
    for row in rows:
        owner_id = payload.owner_ids.get(row.id) or row.owner_id
        owner = owners.get(owner_id) if owner_id else None
        if owner is None:
            owner = _owner_agent(db, owner_id)
            owners[owner.id] = owner
        resolved.append((row, owner))
    for row, owner in resolved:
        newly_assigned = row.status == "Pending" or row.owner_id != owner.id
        row.owner_id, row.owner = owner.id, owner.name
        row.status, row.submitted_at, row.updated_at = "Assigned", now, now
        if newly_assigned:
            description = row.name or row.phone or row.sm_lead_id
            db.add(FDTaskNotification(
                id=uuid4(), recipient_id=owner.id, title="SM Lead assigned to you",
                message=f"SM lead {row.sm_lead_id} ({description}) has been assigned to you. Please call the client and check your SM Leads page.",
                notification_type="sm_lead_assignment", source_page="sm-leads",
                entity_id=str(row.id), created_at=now,
            ))
    db.commit()
    return {"submitted": len(rows), "items": _serialize_ids(db, payload.ids)}


@router.delete("/bulk")
def delete_sm_leads(payload: SMLeadIds, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _require_manager(user, db)
    rows = db.query(FDSMLead).filter(FDSMLead.id.in_(payload.ids)).all()
    if len(rows) != len(set(payload.ids)):
        raise HTTPException(status_code=404, detail="One or more selected SM Leads were not found.")
    for row in rows:
        db.delete(row)
    db.commit()
    return {"deleted": len(rows)}
