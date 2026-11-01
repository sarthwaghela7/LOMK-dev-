"""FlintDesk Dependencies API routes."""

from datetime import date as date_type, datetime, timezone
from typing import Optional
from uuid import UUID
from sqlalchemy import or_

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, desc, func
from sqlalchemy.orm import Session, joinedload

from middleware.auth import get_current_user
from storage.db import get_db
from storage.models import Call, Dependency, CallAnalysisResult, FDLead
from services.flintdesk_notifications import notify_dependency_reviewers, add_notifications
from utils.call_id_formatter import extract_call_number

router = APIRouter(prefix="/api/flintdesk/dependencies", tags=["FlintDesk Dependencies"])

REQUIRED_AGENT_FIELDS = {
    "call_id": "Call ID",
    "client_enquiry_date": "Client Enquiry Date",
    "question": "Question",
    "agent_enquiry_date": "Agent Enquiry Date",
    "agent_research_about_query": "Agent Research About the Query",
}


class DependencyCreate(BaseModel):
    call_id: Optional[str] = None
    client_enquiry_date: Optional[date_type] = None
    call_summary: Optional[str] = None
    question: Optional[str] = None
    agent_enquiry_date: Optional[date_type] = None
    agent_research_about_query: Optional[str] = None


class DependencyUpdate(BaseModel):
    call_id: Optional[str] = None
    client_enquiry_date: Optional[date_type] = None
    call_summary: Optional[str] = None
    question: Optional[str] = None
    agent_enquiry_date: Optional[date_type] = None
    agent_research_about_query: Optional[str] = None
    subject_matter_expert: Optional[str] = None
    subject_matter_expert_comment: Optional[str] = None
    status: Optional[str] = None


class DependencyResponse(BaseModel):
    id: UUID
    call_id: Optional[str]
    client_id: Optional[str] = None
    lead_id: Optional[str] = None
    client_enquiry_date: Optional[date_type]
    call_summary: Optional[str]
    question: Optional[str]
    agent_enquiry_date: Optional[date_type]
    agent_name: Optional[str] = None
    agent_research_about_query: Optional[str]
    subject_matter_expert: Optional[str]
    subject_matter_expert_comment: Optional[str]
    status: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


def _is_reviewer(user: dict) -> bool:
    """Only persisted fd_users reviewer roles may approve dependency input."""
    role = str(user.get("role", "")).strip().lower().replace(" ", "_")
    return role in {"admin", "team_lead", "subject_matter_expert", "superuser", "super_user"}


def _dependency_identifiers(dependency: Dependency, db: Session) -> tuple[str | None, str | None]:
    requested = str(dependency.call_id or "").strip()
    if not requested:
        return None, None
    filters = [Call.call_reference == requested]
    try:
        filters.append(Call.call_id == UUID(requested))
    except (ValueError, TypeError):
        pass
    call_number = extract_call_number(requested)
    if call_number is not None:
        filters.append(Call.call_number == call_number)
    call = db.query(Call).filter(or_(*filters)).first()
    if not call:
        return None, None
    lead = db.query(FDLead).filter(FDLead.fd_a_leads_call_id == call.call_id).first()
    return call.client_id, lead.fd_m_leads_lead_id if lead else None


def _serialize_dependency(dependency: Dependency, db: Session, identifiers: tuple[str | None, str | None] | None = None) -> dict:
    client_id, lead_id = identifiers if identifiers is not None else _dependency_identifiers(dependency, db)
    return {
        "id": dependency.id,
        "call_id": dependency.call_id,
        "client_id": client_id,
        "lead_id": lead_id,
        "client_enquiry_date": dependency.client_enquiry_date,
        "call_summary": dependency.call_summary,
        "question": dependency.question,
        "agent_enquiry_date": dependency.agent_enquiry_date,
        "agent_name": dependency.agent.name if dependency.agent else None,
        "agent_research_about_query": dependency.agent_research_about_query,
        "subject_matter_expert": dependency.subject_matter_expert,
        "subject_matter_expert_comment": dependency.subject_matter_expert_comment,
        "status": dependency.status or "pending",
        "created_at": dependency.created_at,
        "updated_at": dependency.updated_at,
    }


def _dependency_identifier_map(dependencies: list[Dependency], db: Session) -> dict[UUID, tuple[str | None, str | None]]:
    """Resolve identifiers for a dependency page in two queries, not per row."""
    raw_ids = {str(item.call_id or "").strip() for item in dependencies} - {""}
    uuids, numbers = [], []
    for raw_id in raw_ids:
        try:
            uuids.append(UUID(raw_id))
        except (ValueError, TypeError):
            pass
        number = extract_call_number(raw_id)
        if number is not None:
            numbers.append(number)
    calls = db.query(Call).filter(or_(
        Call.call_reference.in_(raw_ids),
        Call.call_id.in_(uuids) if uuids else False,
        Call.call_number.in_(numbers) if numbers else False,
    )).all() if raw_ids else []
    calls_by_identifier = {}
    for call in calls:
        calls_by_identifier[str(call.call_id)] = call
        if call.call_reference:
            calls_by_identifier[str(call.call_reference)] = call
        if call.call_number is not None:
            calls_by_identifier[str(call.call_number)] = call
            calls_by_identifier[f"AF#{call.call_number}"] = call
    call_ids = [call.call_id for call in calls]
    leads_by_call = {
        lead.fd_a_leads_call_id: lead.fd_m_leads_lead_id
        for lead in db.query(FDLead).filter(FDLead.fd_a_leads_call_id.in_(call_ids)).all()
    } if call_ids else {}
    return {
        item.id: (
            calls_by_identifier[raw].client_id,
            leads_by_call.get(calls_by_identifier[raw].call_id),
        ) if (raw := str(item.call_id or "").strip()) in calls_by_identifier else (None, None)
        for item in dependencies
    }


def _missing_agent_fields(dependency: Dependency, payload: DependencyUpdate | None = None) -> list[str]:
    """Return human-readable agent fields that must exist before approval."""
    values = {field: getattr(dependency, field) for field in REQUIRED_AGENT_FIELDS}
    if payload is not None:
        for field in REQUIRED_AGENT_FIELDS:
            incoming = getattr(payload, field)
            if incoming is not None:
                values[field] = incoming
    return [label for field, label in REQUIRED_AGENT_FIELDS.items() if values[field] is None or not str(values[field]).strip()]


@router.get("/", response_model=list[DependencyResponse])
def get_dependencies(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
    pending: bool = Query(False, description="Filter to show only pending (incomplete) dependencies"),
    completed: bool = Query(False, description="Filter to show only completed dependencies"),
    call_id: str = Query("", description="Search by Call ID"),
    client_id: str = Query("", description="Search by Client ID"),
    lead_id: str = Query("", description="Search by Lead ID"),
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1, le=500),
):
    """Get all dependencies with optional filters for pending/completed status and Call ID search."""
    query = db.query(Dependency).options(joinedload(Dependency.agent))

    # Apply status filters
    if pending:
        query = query.filter(func.lower(func.coalesce(Dependency.status, "pending")) != "approved")
    elif completed:
        query = query.filter(func.lower(Dependency.status) == "approved")

    # Apply Call ID search
    if call_id.strip():
        query = query.filter(Dependency.call_id.ilike(f"%{call_id.strip()}%"))

    if client_id.strip() or lead_id.strip():
        candidates = query.all()
        identifiers = _dependency_identifier_map(candidates, db)
        client_search = client_id.strip().lower()
        lead_search = lead_id.strip().lower()
        matching_ids = [dependency_id for dependency_id, (resolved_client_id, resolved_lead_id) in identifiers.items()
                        if (not client_search or client_search in str(resolved_client_id or "").lower())
                        and (not lead_search or lead_search in str(resolved_lead_id or "").lower())]
        query = query.filter(Dependency.id.in_(matching_ids))

    # Sort by created_at descending
    query = query.order_by(desc(Dependency.created_at))
    
    if page and page_size:
        query = query.offset((page - 1) * page_size).limit(page_size)
    dependencies = query.all()
    identifiers = _dependency_identifier_map(dependencies, db)
    return [_serialize_dependency(dependency, db, identifiers.get(dependency.id, (None, None))) for dependency in dependencies]


@router.get("/dashboard-summary")
def dependency_dashboard_summary(
    start_date: date_type | None = None,
    end_date: date_type | None = None,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return lightweight dependency counts for the live Dashboard."""
    query = db.query(
        func.count(Dependency.id).label("total"),
        func.sum(case((func.lower(func.coalesce(Dependency.status, "pending")) == "pending", 1), else_=0)).label("pending"),
        func.sum(case((func.lower(Dependency.status) == "approved", 1), else_=0)).label("approved"),
        func.sum(case((func.lower(Dependency.status) == "rejected", 1), else_=0)).label("rejected"),
    )
    if start_date:
        query = query.filter(Dependency.client_enquiry_date >= start_date)
    if end_date:
        query = query.filter(Dependency.client_enquiry_date <= end_date)
    row = query.one()
    return {key: int(getattr(row, key) or 0) for key in ("total", "pending", "approved", "rejected")}


@router.post("/", response_model=DependencyResponse)
def create_dependency(
    payload: DependencyCreate,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new dependency record."""
    try:
        agent_id = UUID(str(user.get("fd_user_id")))
    except (TypeError, ValueError):
        agent_id = None
    dependency = Dependency(
        call_id=payload.call_id,
        client_enquiry_date=payload.client_enquiry_date,
        call_summary=payload.call_summary,
        question=payload.question,
        agent_enquiry_date=payload.agent_enquiry_date,
        agent_id=agent_id,
        agent_research_about_query=payload.agent_research_about_query,
    )
    db.add(dependency)
    db.flush()
    notify_dependency_reviewers(
        db,
        exclude_id=agent_id,
        title="Dependency needs review",
        message=f"A dependency was submitted for {dependency.call_id or 'an unlinked call'}.",
        notification_type="dependency_submitted",
        source_page="dependencies",
        entity_id=dependency.id,
        change_context={"action": "created", "call_id": dependency.call_id, "agent_id": str(agent_id) if agent_id else None, "fields": sorted(payload.model_fields_set)},
    )
    db.commit()
    db.refresh(dependency)
    return _serialize_dependency(dependency, db)


@router.get("/{dependency_id}", response_model=DependencyResponse)
def get_dependency(
    dependency_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a specific dependency by ID."""
    dependency = db.query(Dependency).filter(Dependency.id == dependency_id).first()
    if not dependency:
        raise HTTPException(status_code=404, detail="Dependency not found")
    return _serialize_dependency(dependency, db)


@router.patch("/{dependency_id}", response_model=DependencyResponse)
def update_dependency(
    dependency_id: str,
    payload: DependencyUpdate,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a dependency record with server-side role checks."""
    dependency = db.query(Dependency).filter(Dependency.id == dependency_id).first()
    if not dependency:
        raise HTTPException(status_code=404, detail="Dependency not found")

    reviewer_fields_requested = any(value is not None for value in (
        payload.subject_matter_expert,
        payload.subject_matter_expert_comment,
        payload.status,
    ))
    if reviewer_fields_requested and not _is_reviewer(user):
        raise HTTPException(status_code=403, detail="Only dependency reviewers can update approval fields")
    if not _is_reviewer(user):
        try:
            actor_agent_id = UUID(str(user.get("fd_user_id")))
        except (TypeError, ValueError):
            actor_agent_id = None
        if dependency.agent_id != actor_agent_id:
            raise HTTPException(status_code=403, detail="You can view all dependencies but only update your own")
    if payload.status is not None and payload.status.lower() != "approved":
        raise HTTPException(status_code=400, detail="Dependency status can only be approved")
    if payload.status is not None:
        missing = _missing_agent_fields(dependency, payload)
        if missing:
            raise HTTPException(status_code=422, detail=f"Complete the agent data before approval: {', '.join(missing)}")

    changed_fields = sorted(payload.model_fields_set)
    actor_id = None
    try:
        actor_id = UUID(str(user.get("fd_user_id")))
    except (TypeError, ValueError):
        pass

    # Update user-editable fields (allowed for all)
    if payload.call_id is not None:
        dependency.call_id = payload.call_id
    if payload.client_enquiry_date is not None:
        dependency.client_enquiry_date = payload.client_enquiry_date
    if payload.call_summary is not None:
        dependency.call_summary = payload.call_summary
    if payload.question is not None:
        dependency.question = payload.question
    if payload.agent_enquiry_date is not None:
        dependency.agent_enquiry_date = payload.agent_enquiry_date
    if payload.agent_research_about_query is not None:
        dependency.agent_research_about_query = payload.agent_research_about_query

    if _is_reviewer(user):
        if payload.subject_matter_expert is not None:
            dependency.subject_matter_expert = payload.subject_matter_expert
        if payload.subject_matter_expert_comment is not None:
            dependency.subject_matter_expert_comment = payload.subject_matter_expert_comment
        if payload.status is not None:
            dependency.status = "approved"

    dependency.updated_at = datetime.now(timezone.utc)
    if payload.status is not None and dependency.agent_id:
        add_notifications(
            db,
            [dependency.agent_id],
            title="Dependency approved",
            message=f"Your dependency for {dependency.call_id or 'an unlinked call'} was approved.",
            notification_type="dependency_approved",
            source_page="dependencies",
            entity_id=dependency.id,
            change_context={"action": "approved", "call_id": dependency.call_id, "reviewer_id": str(actor_id) if actor_id else None, "fields": changed_fields},
        )
    elif changed_fields and not _is_reviewer(user):
        notify_dependency_reviewers(
            db,
            exclude_id=actor_id,
            title="Dependency updated",
            message=f"Agent data changed for dependency {dependency.call_id or dependency.id}.",
            notification_type="dependency_updated",
            source_page="dependencies",
            entity_id=dependency.id,
            change_context={"action": "updated", "call_id": dependency.call_id, "agent_id": str(actor_id) if actor_id else None, "fields": changed_fields},
        )

    db.commit()
    db.refresh(dependency)
    return _serialize_dependency(dependency, db)


@router.delete("/{dependency_id}")
def delete_dependency(
    dependency_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a dependency; destructive changes are restricted to team leads."""
    if not _is_reviewer(user):
        raise HTTPException(status_code=403, detail="Only admins can delete dependencies")
    dependency = db.query(Dependency).filter(Dependency.id == dependency_id).first()
    if not dependency:
        raise HTTPException(status_code=404, detail="Dependency not found")
    db.delete(dependency)
    db.commit()
    return {"deleted": True, "id": dependency_id}


@router.get("/analyse/call-summary")
def get_call_summary_by_id_and_date(
    call_id: str = Query(..., description="Call ID to look up"),
    client_enquiry_date: str = Query(..., description="Client enquiry date (YYYY-MM-DD)"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Fetch the Call Summary for a dependency using Call ID + Client Enquiry Date.
    
    This reuses the existing call_analysis_results data by looking up the call
    using the provided Call ID and matching it against the client_enquiry_date.
    """
    if not call_id.strip():
        raise HTTPException(status_code=400, detail="call_id is required")
    if not client_enquiry_date.strip():
        raise HTTPException(status_code=400, detail="client_enquiry_date is required")

    try:
        # Parse the date to validate format
        from datetime import datetime
        parsed_date = datetime.strptime(client_enquiry_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    # Dependencies store the human-facing AF# number, while analysis rows use
    # the raw UUID plus call_reference/call_number. Resolve all supported
    # identifiers before applying the date constraint.
    requested_id = call_id.strip()
    identifiers = [CallAnalysisResult.call_reference == requested_id]
    try:
        UUID(requested_id)
        identifiers.append(CallAnalysisResult.call_id == requested_id)
    except (ValueError, TypeError):
        pass
    call_number = extract_call_number(requested_id)
    if call_number is not None:
        identifiers.append(CallAnalysisResult.call_number == call_number)

    call_analysis = db.query(CallAnalysisResult).filter(
        or_(*identifiers),
        CallAnalysisResult.call_recording_date_normalized == parsed_date,
    ).first()

    # Some legacy analysis rows have only a free-text recording date. If the
    # ID resolved but the normalized date is absent, compare parsed dates in
    # Python rather than incorrectly reporting that the call does not exist.
    if not call_analysis:
        candidates = db.query(CallAnalysisResult).filter(or_(*identifiers)).all()
        def matches_requested_date(candidate):
            value = str(candidate.call_recording_date or "").strip()
            for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
                try:
                    return datetime.strptime(value[:10], date_format).date() == parsed_date
                except ValueError:
                    continue
            return False

        call_analysis = next((candidate for candidate in candidates if matches_requested_date(candidate)), None)

    if not call_analysis:
        return {"call_summary": None, "message": "No matching call found."}

    # Return the call summary (prefer fd_a_call_summary, fallback to call_summary)
    call_summary = call_analysis.fd_a_call_summary or call_analysis.call_summary or None
    return {
        "call_summary": call_summary,
        "call_id": call_id,
        "client_enquiry_date": client_enquiry_date,
    }
