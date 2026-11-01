"""
Server-side proxy for the third-party STT (speech-to-text) and LLM APIs.

These endpoints exist so the frontend never has direct access to the
STT/LLM provider API keys. The keys live only in the backend's .env
(STT_API_KEY, LLM_API_KEY, etc. via utils.config) and are attached to the
outbound request here; the browser only ever talks to this backend.
"""
import logging
import requests
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from starlette.concurrency import run_in_threadpool

from middleware.auth import get_current_user
from utils.config import STT_API_KEY, STT_MODEL, STT_BASE_URL, LLM_API_KEY, LLM_URL, LLM_MODEL

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai", tags=["AI Proxy"])


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    diarize: str = Form("true"),
    user=Depends(get_current_user),
):
    """Proxies an ad-hoc audio file to the STT provider for one-off transcription
    (used by the standalone Transcript tool, separate from the call pipeline)."""
    if not STT_API_KEY or not STT_BASE_URL:
        raise HTTPException(status_code=500, detail="STT is not configured on the server.")

    audio_bytes = await file.read()
    try:
        response = await run_in_threadpool(
            requests.post, STT_BASE_URL,
            headers={"xi-api-key": STT_API_KEY},
            files={"file": (file.filename, audio_bytes, file.content_type or "audio/mpeg")},
            data={"model_id": STT_MODEL, "diarize": diarize}, timeout=180,
        )
    except requests.RequestException as exc:
        logger.warning("STT request failed: %s", exc)
        raise HTTPException(status_code=502, detail="STT request failed.") from exc

    if not response.ok:
        logger.warning("STT provider error %s: %s", response.status_code, response.text[:500])
        raise HTTPException(status_code=502, detail=f"STT request failed ({response.status_code}).")

    return response.json()


@router.post("/narrative")
def generate_narrative(payload: dict, user=Depends(get_current_user)):
    """Proxies a report-narrative generation request to the LLM provider.
    Expects {"system_prompt": str, "user_content": str} and returns the raw
    JSON text content produced by the model (the frontend parses it)."""
    if not LLM_API_KEY or not LLM_URL:
        raise HTTPException(status_code=500, detail="LLM is not configured on the server.")

    system_prompt = payload.get("system_prompt")
    user_content = payload.get("user_content")
    if not system_prompt or not user_content:
        raise HTTPException(status_code=400, detail="system_prompt and user_content are required.")

    try:
        response = requests.post(
            LLM_URL,
            headers={
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "max_completion_tokens": 1500,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            },
            timeout=120,
        )
    except requests.RequestException as exc:
        logger.warning("LLM request failed: %s", exc)
        raise HTTPException(status_code=502, detail="LLM request failed.") from exc

    if not response.ok:
        logger.warning("LLM provider error %s: %s", response.status_code, response.text[:500])
        raise HTTPException(status_code=502, detail=f"LLM request failed ({response.status_code}).")

    data = response.json()
    text_block = (data.get("choices") or [{}])[0].get("message", {}).get("content")
    if not text_block:
        raise HTTPException(status_code=502, detail="LLM returned no text content.")

    return {"content": text_block}
