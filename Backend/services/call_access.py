"""Shared server-side authorization for agent-managed call data."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from storage.models import Call, FDAssignment


PRIVILEGED_CALL_ROLES = {
    "admin",
    "team_lead",
    "teamlead",
    "subject_matter_expert",
    "sme",
    "superuser",
    "super_user",
}


def is_inbound_call(call: Call, db: Session) -> bool:
    """True for inbound work that is SHARED (no owning agent).

    Use stored routing data, never a client-supplied direction, for access.
    Vitel now reports the answering extension, so an inbound call ANSWERED by
    an agent extension has an owner and follows the same rules as an outbound
    call (assigned-agent edit access, own-name rule). Unanswered or
    non-agent inbound legs remain shared.
    """
    if not getattr(call, "src", None) or not getattr(call, "dst", None):
        return False
    # Import lazily because the serialization routes also use this service.
    from routes.calls import _call_direction, _system_numbers
    from services.vitel_metadata import inbound_agent_extension

    if inbound_agent_extension(call):
        return False
    return _call_direction(call, _system_numbers(db)) == "Inbound"


def assert_manual_call_access(call: Call, user: dict, db: Session) -> None:
    """Allow privileged roles, shared inbound work, or an assigned agent.

    Extension assignments are persistent Settings records.  Requiring their
    creation date to equal a historical call date incorrectly blocks an agent
    from updating a call on an extension they currently own.
    """
    role = str(user.get("role", "agent")).strip().lower().replace(" ", "_").replace("-", "_")
    if role in PRIVILEGED_CALL_ROLES:
        return

    try:
        agent_id = UUID(str(user.get("fd_user_id")))
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(status_code=403, detail="You do not have access to this call.")

    # Inbound calls are shared work: all authenticated agents may fill their
    # stages and manage attachments, regardless of the answering extension.
    if is_inbound_call(call, db):
        return

    extension = str(call.extension or "").strip()
    assignment = (
        db.query(FDAssignment.id)
        .filter(
            FDAssignment.agent_id == agent_id,
            FDAssignment.extension == extension,
            FDAssignment.is_active.is_(True),
        )
        .first()
    )
    if assignment:
        return
    # An agent whose name the Agent column shows on this call owns it, even if
    # the extension has since been reassigned: the column uses the assignment
    # in effect on the call's date. Without this, an agent could fill a field
    # on their own call and then be refused when they came back to update it.
    if _is_shown_owner(call, agent_id, db):
        return
    raise HTTPException(status_code=403, detail="You do not have access to this call.")


def _is_shown_owner(call: Call, agent_id: UUID, db: Session) -> bool:
    """True when the assignment effective on the call's date belongs to this agent."""
    if not getattr(call, "call_id", None) or not getattr(call, "call_date", None):
        return False
    # Import lazily because the serialization routes also use this service.
    from routes.calls import assignment_agent_details

    owner = assignment_agent_details(db, [call]).get(str(call.call_id))
    return bool(owner and owner[0] and owner[0] == str(agent_id))


def assert_completion_access(call: Call, user: dict, db: Session) -> None:
    """Allow completion by privileged users or the call extension's owner.

    Unlike ordinary manual edits, completion is not shared for inbound calls:
    it certifies that the assigned owner has finished the case.
    """
    role = str(user.get("role", "agent")).strip().lower().replace(" ", "_").replace("-", "_")
    if role in PRIVILEGED_CALL_ROLES:
        return

    try:
        agent_id = UUID(str(user.get("fd_user_id")))
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(status_code=403, detail="You do not have access to complete this call.")

    assignment = (
        db.query(FDAssignment.id)
        .filter(
            FDAssignment.agent_id == agent_id,
            FDAssignment.extension == str(call.extension or "").strip(),
            FDAssignment.is_active.is_(True),
        )
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=403, detail="Only the assigned agent can mark this call as completed.")
