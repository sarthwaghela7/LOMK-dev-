import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from middleware.auth import get_current_user
from storage.db import get_db
from storage.models import Call
from storage.s3 import S3Service
from utils.call_id_formatter import format_call_id

router = APIRouter(
    prefix="/audio",
    tags=["Audio"]
)


@router.get("/{call_id}")
def get_audio(
    call_id: UUID,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Returns a temporary S3 URL for the requested call.

    Flow:
    1. Search the call in the database.
    2. Read its S3 URI.
    3. Generate a temporary (presigned) URL.
    4. Return it to the frontend.
    """

    # -----------------------------
    # Find call in database
    # -----------------------------
    call = (
        db.query(Call)
        .filter(Call.call_id == call_id)
        .first()
    )

    if call is None:
        raise HTTPException(
            status_code=404,
            detail="Call not found."
        )

    # -----------------------------
    # Check whether audio exists
    # -----------------------------
    if not call.audio_s3_uri:
        raise HTTPException(
            status_code=404,
            detail="Audio file not available."
        )

    # -----------------------------
    # Generate temporary S3 URL
    # -----------------------------
    s3 = S3Service()

    # Name the file a browser downloads it as after the call's AF# id and
    # the date it happened, instead of the raw S3 object key.
    call_label = format_call_id(call.call_number)
    call_date = call.call_date.isoformat() if call.call_date else "unknown-date"
    download_filename = re.sub(r'[\\/:*?"<>|]', "-", f"{call_label}_{call_date}.wav")

    audio_url = s3.generate_presigned_url(
        call.audio_s3_uri,
        expires_in=3600,     # Valid for 1 hour
        response_content_disposition=f'attachment; filename="{download_filename}"',
    )

    # -----------------------------
    # Return URL
    # -----------------------------
    return {
        "call_id": str(call.call_id),
        "audio_url": audio_url
    }