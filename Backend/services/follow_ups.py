"""Follow Ups: the Success PC calls and the Team Lead Fills data kept for each (table fd_follow_up_retainer, migration 077).

Scope is the canonical Calls-page records whose Case Status Change is Success PC (services.analysis_read_query.analysis_read), so the
tab and the Calls page can never disagree. Everyone signed in sees every call and its Team Lead Fills values (agents view only);
only Admin / Team Lead / SME edit them. Submit assigns the call to its Retainer Owner (the same pattern as assigning a lead) and
notifies them. A submitted call also reports whether its owner already phoned the client the same business day.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

CENTRAL = ZoneInfo("America/Chicago")
SUCCESS_PC = "Success PC"
EDITOR_ROLES = {"admin", "team_lead", "teamlead", "subject_matter_expert", "subjectmatterexpert", "sme", "superuser", "super_user"}

BUSINESS_STATUSES = ("PC Only", "PC + Retainer")
YES_NO = ("Yes", "No")
ACTION_STATUSES = ("Pending", "Completed", "Dropped")
RETAINER_STATUSES = ("Success", "Lost")
MAX_TEXT = 4000
MAX_AMOUNT = Decimal("99999999.99")

# field -> (column, validator). A validator returns the value to store or raises ValueError.
def _choice(options):
    def check(value):
        value = str(value).strip()
        if value not in options:
            raise ValueError(f"must be one of: {', '.join(options)}")
        return value
    return check


def _amount(value):
    try:
        number = Decimal(str(value).replace(",", "").replace("$", "").strip())
    except InvalidOperation as exc:
        raise ValueError("must be a number") from exc
    if not number.is_finite() or number < 0 or number > MAX_AMOUNT:
        raise ValueError("must be between 0 and 99,999,999.99")
    return number.quantize(Decimal("0.01"))


def _text(value):
    value = str(value).strip()
    if len(value) > MAX_TEXT:
        raise ValueError(f"must be {MAX_TEXT} characters or fewer")
    return value


def _owner(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("must be an agent id") from exc


FIELDS = {
    "business_status": _choice(BUSINESS_STATUSES),
    "paid_amount": _amount,
    "retainer_follow_up_required": _choice(YES_NO),
    "retainer_owner_id": _owner,
    "retainer_action_plan": _text,
    "retainer_action_status": _choice(ACTION_STATUSES),
    "retainer_status": _choice(RETAINER_STATUSES),
    # Retainer Verification: no allowed values were specified, so it is stored as free text for now.
    "retainer_verification": _text,
}


def is_editor(user) -> bool:
    role = str(user.get("role", "")).strip().lower().replace(" ", "_").replace("-", "_")
    return role in EDITOR_ROLES


def _right10(value) -> str:
    return re.sub(r"\D", "", str(value or ""))[-10:]


def _clean_updates(updates: dict) -> dict:
    """{column: stored value or None} from a request body; unknown fields and bad values are rejected, blank clears."""
    cleaned, problems = {}, []
    for field, raw in updates.items():
        if field not in FIELDS:
            problems.append(f"{field}: unknown field")
        elif raw is None or str(raw).strip() == "":
            cleaned[field] = None
        else:
            try:
                cleaned[field] = FIELDS[field](raw)
            except ValueError as exc:
                problems.append(f"{field}: {exc}")
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))
    return cleaned


def _assert_success_pc_call(db, call_id: UUID) -> None:
    # call_analysis_results.call_id is a uuid column in production and text in older schemas: compare both sides as text.
    status = db.execute(text("""
        SELECT lower(btrim(coalesce(a.fd_m_case_status_change, ''))) FROM calls c
        LEFT JOIN call_analysis_results a ON a.call_id::text = c.call_id::text WHERE c.call_id = :id
        ORDER BY a.id DESC LIMIT 1"""), {"id": str(call_id)}).first()
    if status is None:
        raise HTTPException(status_code=404, detail="Call not found.")
    if status[0] not in ("success pc", "success - pc"):
        raise HTTPException(status_code=422, detail="Only calls whose Case Status Change is Success PC are in Follow Ups.")


def _cast(column: str) -> str:
    return {"paid_amount": "numeric", "retainer_owner_id": "uuid"}.get(column, "text")


def _active_agent(db, owner_id, prefix: str):
    """The (id, name) of the active Front Desk Agent with this id; 422 for a missing, inactive or non-agent user (same rule as Tasks owners)."""
    owner = db.execute(text("SELECT id, name FROM fd_users WHERE id = CAST(:id AS uuid) AND is_active AND lower(btrim(role)) = 'agent'"),
                       {"id": str(owner_id)}).first()
    if not owner:
        raise HTTPException(status_code=422, detail=f"{prefix}Retainer Owner must be an active Front Desk Agent")
    return owner


def _lock_call(db, call_id: UUID) -> None:
    """Serialise every save and submit of one call for the rest of the transaction, so a reassignment and a Submit (or two saves)
    can never interleave: the second waits and then sees the first one's committed owner."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"follow-up:{call_id}"})


def save_fields(db, call_id: UUID, updates: dict) -> dict:
    """Create or update the Team Lead Fills values of one Success PC call. Only the fields sent change."""
    cleaned = _clean_updates(updates)
    if not cleaned:
        raise HTTPException(status_code=422, detail="Nothing to save.")
    _assert_success_pc_call(db, call_id)
    owner = cleaned.get("retainer_owner_id")
    if owner is not None:
        _active_agent(db, owner, "retainer_owner_id: ")
    _lock_call(db, call_id)
    previous = db.execute(text("SELECT retainer_owner_id::text, submitted_at FROM fd_follow_up_retainer WHERE call_id = CAST(:id AS uuid)"), {"id": str(call_id)}).first()
    columns = list(cleaned)
    params = {"call_id": str(call_id), **{column: (str(value) if isinstance(value, UUID) else value) for column, value in cleaned.items()}}
    # The save, the withdrawal and the notification to the previous owner are one transaction: a failure keeps none of them.
    try:
        db.execute(text(f"""
            INSERT INTO fd_follow_up_retainer (call_id, {", ".join(columns)})
            VALUES (CAST(:call_id AS uuid), {", ".join(f"CAST(:{column} AS {_cast(column)})" for column in columns)})
            ON CONFLICT (call_id) DO UPDATE SET {", ".join(f"{column} = EXCLUDED.{column}" for column in columns)}, updated_at = now()
        """), params)
        # Giving an already submitted call to a different owner (or to nobody) withdraws that submission: Submit must be pressed again
        # so the new owner is told. The previous owner, who has just lost the call, is told too.
        if "retainer_owner_id" in cleaned and previous and (previous[0] or None) != (str(owner) if owner else None):
            db.execute(text("UPDATE fd_follow_up_retainer SET assigned_at = NULL, submitted_at = NULL, submitted_by = NULL WHERE call_id = CAST(:id AS uuid)"), {"id": str(call_id)})
            if previous[0] and previous[1] is not None:
                _notify_unassigned(db, call_id, UUID(previous[0]))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_row(db, call_id)


def _call_reference(db, call_id: UUID) -> str:
    return db.execute(text("SELECT coalesce(call_reference, '') FROM calls WHERE call_id = CAST(:id AS uuid)"), {"id": str(call_id)}).scalar() or str(call_id)[:8]


def _notify_unassigned(db, call_id: UUID, previous_owner: UUID) -> None:
    """Tell the owner a submitted follow up was taken away from that the call is no longer theirs (their earlier assignment alert stays in their bell)."""
    from services.flintdesk_notifications import add_notifications
    add_notifications(
        db, [previous_owner], title="Follow up no longer assigned to you",
        message=f"Call {_call_reference(db, call_id)} was assigned to you as Retainer Owner but has been reassigned. It is no longer assigned to you.",
        notification_type="follow_up_unassigned", source_page="follow-ups", entity_id=call_id,
    )


def _client_phone(db, call_id: UUID) -> str:
    """The client's number of one call (the other party), as the list shows it."""
    from routes.calls import _call_direction, _system_numbers
    call = db.execute(text("SELECT src, dst FROM calls WHERE call_id = CAST(:id AS uuid)"), {"id": str(call_id)}).first()
    if not call:
        return ""
    direction = _call_direction(SimpleNamespace(src=call[0], dst=call[1]), _system_numbers(db))
    return (call[1] if direction == "Outbound" else call[0] if direction == "Inbound" else "") or ""


def get_row(db, call_id: UUID) -> dict:
    """One row exactly as the list returns it (including its same-day "Follow Up Call" marker), so the page can update just this row."""
    row = db.execute(text(_ROW_SQL + " WHERE r.call_id = CAST(:id AS uuid)"), {"id": str(call_id)}).mappings().first()
    if not row:
        return {}
    out = _public(dict(row))
    out["call_id"] = str(call_id)
    out["client_phone"] = _client_phone(db, call_id)
    _attach_same_day_follow_up_calls(db, [out])
    return out


_ROW_SQL = """
    SELECT r.call_id::text AS call_id, r.business_status, r.paid_amount, r.retainer_follow_up_required, r.retainer_owner_id::text AS retainer_owner_id,
           u.name AS retainer_owner_name, r.retainer_action_plan, r.retainer_action_status, r.retainer_status, r.retainer_verification,
           r.assigned_at, r.submitted_at
    FROM fd_follow_up_retainer r LEFT JOIN fd_users u ON u.id = r.retainer_owner_id
"""


def _public(row: dict) -> dict:
    amount = row.get("paid_amount")
    row["paid_amount"] = format(amount, "f") if isinstance(amount, Decimal) else amount
    for key in ("assigned_at", "submitted_at"):
        if isinstance(row.get(key), datetime):
            row[key] = row[key].isoformat()
    row["submitted"] = bool(row.get("submitted_at"))
    return row


def submit(db, call_id: UUID, user: dict) -> dict:
    """Assign the call to its Retainer Owner and tell them. Submitting twice for the same owner notifies once."""
    _assert_success_pc_call(db, call_id)
    _lock_call(db, call_id)
    row = db.execute(text("SELECT retainer_owner_id::text, submitted_at FROM fd_follow_up_retainer WHERE call_id = CAST(:id AS uuid) FOR UPDATE"),
                     {"id": str(call_id)}).first()
    if not row or not row[0]:
        raise HTTPException(status_code=422, detail="Choose a Retainer Owner before submitting.")
    owner = _active_agent(db, row[0], "")      # saved earlier: the owner may have been deactivated or changed role since
    # The submission and its notification are one transaction: if anything fails, neither is kept.
    try:
        if row[1] is None:
            db.execute(text("""UPDATE fd_follow_up_retainer SET assigned_at = now(), submitted_at = now(), submitted_by = CAST(:by AS uuid), updated_at = now()
                               WHERE call_id = CAST(:id AS uuid)"""), {"id": str(call_id), "by": str(user.get("fd_user_id") or "") or None})
            reference = _call_reference(db, call_id)
            from services.flintdesk_notifications import add_notifications
            add_notifications(
                db, [owner[0]], title="Follow up assigned to you",
                message=f"Call {reference} has been assigned to you as Retainer Owner. Please check Assigning the Calls > Follow Ups.",
                notification_type="follow_up_assignment", source_page="follow-ups", entity_id=call_id,
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_row(db, call_id)


def list_follow_ups(db, user: dict, start: date, end: date, agent: str = "all") -> list[dict]:
    """Rows for the Follow Ups tab, newest call first."""
    from routes.calls import _call_direction, _system_numbers
    from services.analysis_read_query import analysis_read

    # The Agent filter is the Retainer Owner (an agent id): the calls submitted / assigned to that agent. A name is still accepted and
    # then narrows by the call's own agent, as before.
    owner_id = None
    call_agent = None if agent in (None, "", "all") else agent
    if call_agent:
        try:
            owner_id, call_agent = str(UUID(str(call_agent))), None
        except ValueError:
            pass
    scope = analysis_read(db, start=start, end=end, call_source=True, target_page="calls", case_status=SUCCESS_PC, ids_only=True, agent=call_agent)
    ids = [str(row[0]) for row in scope]
    if not ids:
        return []
    rows = db.execute(text("""
        SELECT c.call_id::text AS call_id, c.call_date, c.call_time, c.src, c.dst, c.call_reference, c.call_number,
               coalesce(nullif(btrim(cl.client_name), ''), nullif(btrim(a.fd_m_client_name), ''), nullif(btrim(a.fd_a_client_name), '')) AS client_name,
               coalesce(nullif(btrim(a.front_desk_person_name), ''), '') AS agent_name,
               coalesce(nullif(btrim(a.fd_a_case_type), ''), '') AS case_type,
               coalesce(nullif(btrim(a.fd_a_call_summary), ''), '') AS call_summary,
               r.business_status, r.paid_amount, r.retainer_follow_up_required, r.retainer_owner_id::text AS retainer_owner_id, u.name AS retainer_owner_name,
               r.retainer_action_plan, r.retainer_action_status, r.retainer_status, r.retainer_verification, r.assigned_at, r.submitted_at
        FROM calls c
        LEFT JOIN clients cl ON cl.client_id = c.client_id
        LEFT JOIN LATERAL (SELECT * FROM call_analysis_results x WHERE x.call_id::text = c.call_id::text ORDER BY x.id DESC LIMIT 1) a ON true
        LEFT JOIN fd_follow_up_retainer r ON r.call_id = c.call_id
        LEFT JOIN fd_users u ON u.id = r.retainer_owner_id
        WHERE c.call_id = ANY(CAST(:ids AS uuid[])) AND (CAST(:owner AS uuid) IS NULL OR r.retainer_owner_id = CAST(:owner AS uuid))
        ORDER BY c.call_date DESC, c.call_time DESC, c.call_id DESC
    """), {"ids": ids, "owner": owner_id}).mappings().all()
    system_numbers = _system_numbers(db)
    out = []
    for row in rows:
        row = dict(row)
        call = SimpleNamespace(src=row["src"], dst=row["dst"])
        direction = _call_direction(call, system_numbers)
        row["client_phone"] = (row["dst"] if direction == "Outbound" else row["src"] if direction == "Inbound" else "") or ""
        row["date"] = row.pop("call_date").isoformat()
        row["time"] = row.pop("call_time").strftime("%H:%M:%S") if row["call_time"] else ""
        row["call_reference"] = row["call_reference"] or (f"AF#{row['call_number']}" if row["call_number"] else "")
        out.append(_public({key: value for key, value in row.items() if key not in ("src", "dst", "call_number")}))
    _attach_same_day_follow_up_calls(db, out)
    return out


def _attach_same_day_follow_up_calls(db, rows: list[dict]) -> None:
    """Set row["follow_up_call"] = {call_id, call_reference} when the Retainer Owner phoned the client on the day the call was assigned.

    Same business day (Central Time) as the submission; a call made from one of the owner's extensions to the client's number
    (last 10 digits). Never set for a call that has not been submitted.
    """
    candidates = [row for row in rows if row.get("submitted_at") and row.get("retainer_owner_id") and _right10(row.get("client_phone"))]
    for row in rows:
        row["follow_up_call"] = None
    if not candidates:
        return
    owners = sorted({row["retainer_owner_id"] for row in candidates})
    extensions: dict[str, set[str]] = {}
    for owner_id, extension in db.execute(text("SELECT agent_id::text, extension FROM fd_assignments WHERE agent_id = ANY(CAST(:ids AS uuid[]))"), {"ids": owners}):
        extensions.setdefault(owner_id, set()).add(str(extension or "").strip())
    for row in candidates:
        row["_day"] = datetime.fromisoformat(row["submitted_at"]).astimezone(CENTRAL).date()
    days = sorted({row["_day"] for row in candidates})
    made = db.execute(text("""
        SELECT call_id::text, call_reference, call_date, btrim(coalesce(extension, '')) AS extension, dst FROM calls
        WHERE call_date = ANY(:days) AND btrim(coalesce(extension, '')) <> ''
    """), {"days": days}).mappings().all()
    for row in candidates:
        phone = _right10(row["client_phone"])
        mine = extensions.get(row["retainer_owner_id"], set())
        match = next((call for call in made if call["call_date"] == row["_day"] and call["extension"] in mine
                      and _right10(call["dst"]) == phone and call["call_id"] != row["call_id"]), None)
        if match:
            row["follow_up_call"] = {"call_id": match["call_id"], "call_reference": match["call_reference"] or ""}
    for row in candidates:
        row.pop("_day", None)
