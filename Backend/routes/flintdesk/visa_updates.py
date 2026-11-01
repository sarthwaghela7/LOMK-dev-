import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from middleware.auth import get_current_user, require_fd_visa_update_editor
from storage.db import get_db
from storage.models import (
    FDVisaUpdate, FDVisaUpdateAcknowledgement, FDVisaUpdateQuestion,
    FDVisaUpdateResponse, FDUser,
)
from services.flintdesk_notifications import notify_dependency_reviewers

router = APIRouter(prefix="/api/flintdesk/visa-updates", tags=["FlintDesk Visa Updates"])
QUESTION_TYPES = {"single_select", "multiple_select", "short_answer", "true_false"}


class QuestionCreate(BaseModel):
    question: str
    question_type: str
    options: list[str] | None = None
    correct_answer: Any = None
    required: bool = True


class VisaUpdateCreate(BaseModel):
    case_type: str
    title: str
    details: str
    reference_link: str | None = None
    update_date: date
    questions: list[QuestionCreate] = Field(default_factory=list)


class AnswerPayload(BaseModel):
    question_id: UUID
    answer: Any


class AcknowledgePayload(BaseModel):
    answers: list[AnswerPayload] = Field(default_factory=list)


def current_user_id(user: dict) -> UUID:
    try:
        return UUID(str(user["fd_user_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(401, "FlintDesk user identity is required") from exc


def required_text(value: str, label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise HTTPException(422, f"{label} is required")
    return clean


def validate_url(value: str | None) -> str | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(422, "Reference link must be a valid HTTP or HTTPS URL")
    return clean


def serialize_question(question: FDVisaUpdateQuestion) -> dict:
    return {
        "id": str(question.id), "question": question.question,
        "question_type": question.question_type, "options": question.options or [],
        "required": question.required,
    }


def short_description(details: str, limit: int = 180) -> str:
    plain = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", details or "")
    plain = re.sub(r"[#*_>`~-]", "", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain if len(plain) <= limit else f"{plain[:limit].rstrip()}…"


def read_update_ids(db: Session, user_id: UUID, update_ids: list[UUID]) -> set[UUID]:
    if not update_ids:
        return set()
    return {
        row[0] for row in db.query(FDVisaUpdateAcknowledgement.update_id).filter(
            FDVisaUpdateAcknowledgement.user_id == user_id,
            FDVisaUpdateAcknowledgement.update_id.in_(update_ids),
        ).all()
    }


@router.get("")
def list_updates(
    page: int = Query(1, ge=1), page_size: int = Query(5, ge=1, le=50),
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    user_id = current_user_id(user)
    base = db.query(FDVisaUpdate).filter(FDVisaUpdate.status == "published")
    total = base.count()
    updates = base.order_by(FDVisaUpdate.update_date.desc(), FDVisaUpdate.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    read_ids = read_update_ids(db, user_id, [item.id for item in updates])
    return {
        "items": [{
            "id": str(item.id), "case_type": item.case_type, "title": item.title,
            "short_description": short_description(item.details),
            "update_date": item.update_date.isoformat(), "created_at": item.created_at.isoformat(),
            "read_status": "read" if item.id in read_ids else "unread",
        } for item in updates],
        "page": page, "page_size": page_size, "total": total,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@router.get("/counts")
def get_counts(user=Depends(get_current_user), db: Session = Depends(get_db)):
    user_id = current_user_id(user)
    total = db.query(func.count(FDVisaUpdate.id)).filter(FDVisaUpdate.status == "published").scalar() or 0
    read = db.query(func.count(FDVisaUpdateAcknowledgement.id)).join(
        FDVisaUpdate, FDVisaUpdate.id == FDVisaUpdateAcknowledgement.update_id,
    ).filter(FDVisaUpdateAcknowledgement.user_id == user_id, FDVisaUpdate.status == "published").scalar() or 0
    return {"read": read, "unread": max(0, total - read), "total": total}


@router.get("/{update_id}")
def get_update(update_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    user_id = current_user_id(user)
    update = db.query(FDVisaUpdate).filter(FDVisaUpdate.id == update_id, FDVisaUpdate.status == "published").first()
    if not update:
        raise HTTPException(404, "Visa update not found")
    acknowledgement = db.query(FDVisaUpdateAcknowledgement).filter_by(user_id=user_id, update_id=update.id).first()
    questions = db.query(FDVisaUpdateQuestion).filter_by(update_id=update.id).order_by(FDVisaUpdateQuestion.position).all()
    return {
        "id": str(update.id), "case_type": update.case_type, "title": update.title,
        "details": update.details, "reference_link": update.reference_link,
        "update_date": update.update_date.isoformat(), "created_at": update.created_at.isoformat(),
        "read_status": "read" if acknowledgement else "unread",
        "read_at": acknowledgement.read_at.isoformat() if acknowledgement else None,
        "questions": [] if acknowledgement else [serialize_question(question) for question in questions],
    }


@router.post("", status_code=201, dependencies=[Depends(require_fd_visa_update_editor)])
def create_update(payload: VisaUpdateCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    update = FDVisaUpdate(
        id=uuid4(), case_type=required_text(payload.case_type, "Case type"),
        title=required_text(payload.title, "Update title"), details=required_text(payload.details, "Update details"),
        reference_link=validate_url(payload.reference_link), update_date=payload.update_date,
        status="published", created_by=current_user_id(user), created_at=datetime.now(timezone.utc),
    )
    db.add(update)
    for position, item in enumerate(payload.questions):
        question_type = str(item.question_type or "").strip().lower()
        if question_type not in QUESTION_TYPES:
            raise HTTPException(422, "Unsupported acknowledgement question type")
        options = [str(option).strip() for option in (item.options or []) if str(option).strip()]
        if question_type in {"single_select", "multiple_select"} and len(options) < 2:
            raise HTTPException(422, "Select questions require at least two options")
        db.add(FDVisaUpdateQuestion(
            id=uuid4(), update_id=update.id, question=required_text(item.question, "Question"),
            question_type=question_type, options=options or None,
            correct_answer=item.correct_answer, required=item.required, position=position,
        ))
    db.commit(); db.refresh(update)
    return {"id": str(update.id), "message": "Visa update published"}


def has_answer(question: FDVisaUpdateQuestion, answer: Any) -> bool:
    if question.question_type == "multiple_select":
        return isinstance(answer, list) and any(str(value).strip() for value in answer)
    if question.question_type == "true_false":
        return isinstance(answer, bool)
    return bool(str(answer or "").strip())


@router.post("/{update_id}/acknowledge")
def acknowledge_update(update_id: UUID, payload: AcknowledgePayload, user=Depends(get_current_user), db: Session = Depends(get_db)):
    user_id = current_user_id(user)
    update = db.query(FDVisaUpdate).filter(FDVisaUpdate.id == update_id, FDVisaUpdate.status == "published").first()
    if not update:
        raise HTTPException(404, "Visa update not found")
    existing = db.query(FDVisaUpdateAcknowledgement).filter_by(user_id=user_id, update_id=update.id).first()
    if existing:
        return {"status": "read", "read_at": existing.read_at.isoformat()}
    questions = db.query(FDVisaUpdateQuestion).filter_by(update_id=update.id).order_by(FDVisaUpdateQuestion.position).all()
    answers = {answer.question_id: answer.answer for answer in payload.answers}
    valid_ids = {question.id for question in questions}
    if any(question_id not in valid_ids for question_id in answers):
        raise HTTPException(422, "An answer references an unknown question")
    missing = [question.question for question in questions if question.required and not has_answer(question, answers.get(question.id))]
    if missing:
        raise HTTPException(422, f"Answer all required questions: {', '.join(missing)}")
    now = datetime.now(timezone.utc)
    for question in questions:
        if question.id not in answers or not has_answer(question, answers[question.id]):
            continue
        answer = answers[question.id]
        options = question.options or []
        if question.question_type == "single_select" and answer not in options:
            raise HTTPException(422, f"Invalid answer for: {question.question}")
        if question.question_type == "multiple_select" and (not isinstance(answer, list) or any(value not in options for value in answer)):
            raise HTTPException(422, f"Invalid answer for: {question.question}")
        db.add(FDVisaUpdateResponse(
            id=uuid4(), user_id=user_id, update_id=update.id, question_id=question.id,
            answer=answer, submitted_at=now,
        ))
    db.add(FDVisaUpdateAcknowledgement(id=uuid4(), user_id=user_id, update_id=update.id, read_at=now))
    agent = db.query(FDUser).filter(FDUser.id == user_id).first()
    agent_name = (agent.name if agent else str(user.get("name") or "An agent")).strip() or "An agent"
    # The acknowledgement uniqueness guard above makes this notification
    # idempotent: repeated clicks return before a second event is created.
    notify_dependency_reviewers(
        db,
        title="Visa Update Read",
        message=f"{agent_name} has clicked 'I Have Read' on {update.title}.",
        notification_type="visa_update_acknowledged",
        source_page="visa_updates",
        entity_id=update.id,
        change_context={"visa_update_id": str(update.id), "visa_update_title": update.title, "agent_id": str(user_id), "agent_name": agent_name, "read_at": now.isoformat()},
    )
    db.commit()
    return {"status": "read", "read_at": now.isoformat()}


@router.delete("/{update_id}", dependencies=[Depends(require_fd_visa_update_editor)])
def delete_update(update_id: UUID, db: Session = Depends(get_db)):
    update = db.query(FDVisaUpdate).filter(FDVisaUpdate.id == update_id).first()
    if not update:
        raise HTTPException(404, "Visa update not found")
    # Questions, acknowledgements, and responses cascade via the DB FK ondelete rule.
    db.delete(update)
    db.commit()
    return {"status": "deleted"}
