from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from sqlalchemy import text

from services.Processing_pipeline.fd_llm_node import FDLLM
from middleware.auth import get_current_user
from storage.db import get_db
from storage.models import Call, CallAnalysisResult
from storage.s3 import S3Service

router = APIRouter(prefix="/flintdesk", tags=["FlintDesk"])

FD_AI_FIELDS = [
    "fd_a_client_name",
    "fd_a_client_type",
    "fd_a_lead_source",
    "fd_a_email_address",
    "fd_a_urgency_flag",
    "fd_a_case_type",
    "fd_a_case_worker_assigned",
    "fd_a_call_outcome",
    "fd_a_call_summary",
]

FD_MANUAL_FIELDS = [
    "fd_m_case_status_change",
    "fd_m_date_entry",
    "fd_m_verification_comment",
    "fd_m_attachment",
    "fd_m_google_review",
]

from services.case_lifecycle import CASE_STATUS_OPTIONS, accepted_status
VALID_FD_CASE_STATUS_CHANGE = set(CASE_STATUS_OPTIONS)

ALLOWED_ATTACHMENT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}


class FlintDeskManualFieldUpdateRequest(BaseModel):
    fd_m_case_status_change: Optional[str] = None
    fd_m_date_entry: Optional[date] = None
    fd_m_verification_comment: Optional[str] = None
    fd_m_attachment: Optional[str] = None
    fd_m_google_review: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class FlintDeskAnalysisResponse(BaseModel):
    call_id: str
    fd_processing_status: str
    analysis: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


def _get_call_or_404(call_id: str, db: Session) -> Call:
    call = db.query(Call).filter(Call.call_id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return call


def _get_or_create_analysis_record(call: Call, db: Session) -> CallAnalysisResult:
    analysis = (
        db.query(CallAnalysisResult)
        .filter(CallAnalysisResult.call_id == str(call.call_id))
        .first()
    )
    if analysis:
        return analysis

    analysis = CallAnalysisResult(
        call_id=str(call.call_id),
        call_number=call.call_number,
        call_reference=call.call_reference,
        call_recording_date=str(call.call_date) if call.call_date else None,
        call_duration=str(call.billsec) if call.billsec else None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(analysis)
    return analysis


def _coerce_analysis_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key in FD_AI_FIELDS:
        value = payload.get(key, None)
        if value is None:
            cleaned[key] = None
        elif isinstance(value, str):
            cleaned_value = value.strip()
            cleaned[key] = cleaned_value or None
        elif isinstance(value, (dict, list)):
            cleaned[key] = value
        else:
            cleaned[key] = str(value)
    return cleaned


@router.get("/calls/{call_id}")
def get_flintdesk_call(call_id: str, db: Session = Depends(get_db)):
    """Return the current FlintDesk analysis + manual values for a specific call."""
    call = _get_call_or_404(call_id, db)
    analysis = _get_or_create_analysis_record(call, db)

    return {
        "call_id": str(call.call_id),
        "fd_processing_status": call.fd_processing_status or "not processed",
        "analysis": {key: getattr(analysis, key) for key in FD_AI_FIELDS},
        "manual": {key: getattr(analysis, key) for key in FD_MANUAL_FIELDS},
    }


@router.post("/calls/{call_id}/analyse")
def analyse_flintdesk_call(call_id: str, db: Session = Depends(get_db)):
    """FlintDesk-specific trigger. This intentionally bypasses the AutoFlint pipeline."""
    call = _get_call_or_404(call_id, db)

    if not (call.transcript or "").strip():
        call.fd_processing_status = "not processed"
        call.updated_at = datetime.now(timezone.utc)
        db.add(call)
        db.commit()
        raise HTTPException(status_code=400, detail="Transcript unavailable for FlintDesk analysis.")

    try:
        # AutoFlint continues to use the existing llm_node.py.
        # FlintDesk analysis intentionally uses the dedicated FlintDesk node.
        llm = FDLLM()
        response = llm.generate_flintdesk_analysis(call.transcript or "", call)
        validated = _coerce_analysis_payload(response)
    except Exception as exc:
        call.fd_processing_status = "not processed"
        call.updated_at = datetime.now(timezone.utc)
        db.add(call)
        db.commit()
        raise HTTPException(status_code=502, detail=f"FlintDesk analysis failed: {exc}") from exc

    try:
        analysis = _get_or_create_analysis_record(call, db)
        for key in FD_AI_FIELDS:
            setattr(analysis, key, validated.get(key))
        analysis.raw_llm_json = validated
        analysis.processing_status = "completed"

        call.fd_processing_status = "processed"
        call.error_message = None
        call.updated_at = datetime.now(timezone.utc)

        db.add(call)
        db.add(analysis)
        db.commit()
        db.refresh(analysis)
    except Exception as exc:
        call.fd_processing_status = "not processed"
        call.updated_at = datetime.now(timezone.utc)
        db.add(call)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to save FlintDesk analysis: {exc}") from exc

    return {
        "call_id": str(call.call_id),
        "fd_processing_status": "processed",
        "analysis": {key: getattr(analysis, key) for key in FD_AI_FIELDS},
    }


@router.patch("/calls/{call_id}/manual-fields")
def update_flintdesk_manual_fields(
    call_id: str,
    payload: FlintDeskManualFieldUpdateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update only the manual FlintDesk fields; never touch the AI-generated fd_a_* values."""
    call = _get_call_or_404(call_id, db)
    analysis = _get_or_create_analysis_record(call, db)

    if payload.fd_m_case_status_change is not None:
        value = accepted_status(payload.fd_m_case_status_change, analysis.fd_m_case_status_change)
        if value is None:
            raise HTTPException(status_code=400, detail="Invalid fd_m_case_status_change value.")
        db.add(analysis)
        db.flush()
        if (analysis.fd_m_case_status_change or "").strip() != value:
            db.execute(text("""
                INSERT INTO fd_case_status_history (analysis_id, call_id, previous_status, new_status, changed_by)
                VALUES (:analysis_id, :call_id, :previous_status, :new_status, :changed_by)
            """), {
                "analysis_id": analysis.id,
                "call_id": analysis.call_id,
                "previous_status": analysis.fd_m_case_status_change,
                "new_status": value,
                "changed_by": user.get("fd_user_id"),
            })
        if (analysis.fd_m_case_status_change or "").strip() != value:
            from services.stage_completion import case_status_change_date
            analysis.fd_m_date_entry = case_status_change_date()
        analysis.fd_m_case_status_change = value

    # fd_m_date_entry is system-generated; a client-supplied value is ignored.

    if payload.fd_m_verification_comment is not None:
        analysis.fd_m_verification_comment = payload.fd_m_verification_comment.strip()

    if payload.fd_m_attachment is not None:
        analysis.fd_m_attachment = payload.fd_m_attachment.strip()

    if payload.fd_m_google_review is not None:
        analysis.fd_m_google_review = payload.fd_m_google_review.strip()

    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    return {
        "call_id": str(call.call_id),
        "fd_processing_status": call.fd_processing_status or "not processed",
        "manual": {key: getattr(analysis, key) for key in FD_MANUAL_FIELDS},
    }


@router.post("/calls/{call_id}/attachment")
async def upload_flintdesk_attachment(
    call_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload a FlintDesk attachment to S3 and persist only the S3 reference in fd_m_attachment."""
    call = _get_call_or_404(call_id, db)
    analysis = _get_or_create_analysis_record(call, db)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Attachment filename is required.")

    extension = os.path.splitext(file.filename)[1].lower()
    if extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported attachment type. Allowed: .png, .jpg, .jpeg, .pdf",
        )

    s3_service = S3Service()
    temp_path = None
    try:
        suffix = extension
        key = f"flintdesk/attachments/{call_id}/{uuid.uuid4().hex}{suffix}"
        temp_path = f"/tmp/{uuid.uuid4().hex}{suffix}"
        content = await file.read()
        with open(temp_path, "wb") as fh:
            fh.write(content)

        stored_uri = s3_service.upload_file(temp_path, key)
        analysis.fd_m_attachment = stored_uri
        db.add(analysis)
        db.commit()
        db.refresh(analysis)

        return {
            "call_id": str(call.call_id),
            "fd_m_attachment": stored_uri,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Attachment upload failed: {exc}") from exc
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
