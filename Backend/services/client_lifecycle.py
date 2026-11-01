"""Centralized, transaction-safe client identity and lifecycle handling."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from storage.models import Call, Client, FDSyncFilter

CLIENT_STATUSES = {"Open", "Pending", "Closed", "Reopened"}


def normalize_phone_number(value: object) -> str | None:
    """Return the canonical client matching number or None for unusable values.

    FlintDesk's configured lines are US 10-digit numbers.  The additional 91
    rule keeps the documented Indian examples stable without treating a short
    extension as a client phone number.
    """
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    elif len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits if len(digits) >= 7 else None


def caller_phone_number(db: Session, src: object, dst: object) -> str | None:
    """Choose the external party's number using FlintDesk's configured lines."""
    configured = db.query(FDSyncFilter.value).filter(FDSyncFilter.filter_type == "number").all()
    system_numbers = {normalize_phone_number(value) for (value,) in configured}
    system_numbers.discard(None)
    source = normalize_phone_number(src)
    destination = normalize_phone_number(dst)

    if source in system_numbers and destination not in system_numbers:
        return destination
    if destination in system_numbers and source not in system_numbers:
        return source
    # CDRs that cannot be direction-classified still preserve the source as
    # the caller, matching the application's existing routing convention.
    return source or destination


def _new_client_id(db: Session) -> str:
    sequence_value = db.execute(text("SELECT nextval('clients_client_id_seq')")).scalar_one()
    return f"CL-{sequence_value:06d}"


def resolve_client(db: Session, phone_number: object, call_date: date) -> Client | None:
    """Resolve or create one permanent client and update call lifecycle data.

    The unique database index plus INSERT ... ON CONFLICT handles simultaneous
    first calls without ever issuing two client IDs for the same number.
    The caller owns the surrounding transaction and must commit it with the
    call row, so client totals cannot get ahead of persisted calls.
    """
    normalized = normalize_phone_number(phone_number)
    if not normalized:
        return None

    now = datetime.now(timezone.utc)
    candidate_id = _new_client_id(db)
    created = db.execute(
        insert(Client)
        .values(
            client_id=candidate_id,
            phone_number=normalized,
            first_call_date=call_date,
            last_call_date=call_date,
            total_calls=1,
            client_status="Open",
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["phone_number"])
        .returning(Client.client_id)
    ).scalar_one_or_none()
    if created:
        return db.query(Client).filter(Client.client_id == created).one()

    # Locking makes the increment and Closed -> Reopened transition atomic.
    client = (
        db.query(Client)
        .filter(Client.phone_number == normalized)
        .with_for_update()
        .one()
    )
    client.last_call_date = max(client.last_call_date, call_date)
    client.total_calls += 1
    if client.client_status == "Closed":
        client.client_status = "Reopened"
    client.updated_at = now
    return client


def resolve_client_for_call(db: Session, src: object, dst: object, call_date: date) -> Client | None:
    return resolve_client(db, caller_phone_number(db, src, dst), call_date)


def reconcile_client_call_totals(db: Session, client_ids: Iterable[str]) -> None:
    """Make lifecycle totals match unique physical calls, not duplicate CDR legs."""
    for client_id in {str(value) for value in client_ids if value}:
        calls = db.query(Call).filter(Call.client_id == client_id).all()
        # Vitel may emit revised legs with a different billsec but the same
        # call instant/source/destination. They are one client interaction.
        unique = {
            (call.call_date, call.call_time, str(call.src or "").strip(), str(call.dst or "").strip())
            for call in calls
        }
        client = db.query(Client).filter(Client.client_id == client_id).with_for_update().first()
        if client:
            client.total_calls = len(unique)
