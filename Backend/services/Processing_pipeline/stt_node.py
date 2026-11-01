from pathlib import Path
import requests
import time
import tempfile

from utils.config import STT_API_KEY, STT_MODEL, STT_BASE_URL
from storage.s3 import S3Service
from monitoring.audio import wav_duration_seconds
from monitoring.recorder import report_stt_audio_seconds
from storage.db import SessionLocal
from storage.models import FDUser
from sqlalchemy.exc import SQLAlchemyError

def get_front_desk_keyterms():
    """Load active front desk names so new fd_users affect the next STT call."""
    try:
        with SessionLocal() as db:
            names = db.query(FDUser.name).filter(FDUser.is_active.is_(True)).all()
        return [name.strip() for (name,) in names if name and name.strip()]
    except SQLAlchemyError as error:
        print(f"[STT] Could not load fd_users keyterms: {error}")
        return []

# Maps ElevenLabs' detect_speaker_roles output ("agent"/"customer") to the
# labels we actually want in the transcript for this front desk use case.
SPEAKER_ROLE_LABELS = {
    "agent": "Front Desk",
    "customer": "Client",
}


def _speaker_label(speaker_id):
    if not speaker_id:
        return "Unknown"
    if speaker_id in SPEAKER_ROLE_LABELS:
        return SPEAKER_ROLE_LABELS[speaker_id]
    # Fallback for generic diarization output (e.g. "speaker_0") in case
    # detect_speaker_roles ever fails to resolve a role for a segment.
    return str(speaker_id).replace("_", " ").title()


def _build_labeled_transcript(response_json):
    """
    Turns ElevenLabs' word-level diarized output into a readable transcript
    with speaker turns, e.g.:
        Front Desk: Thank you for calling...
        Client: Hi, I wanted to ask about...
    Falls back to the plain "text" field if no word-level speaker data is
    present (e.g. diarization found only one speaker, or was unavailable).
    """
    words = response_json.get("words")
    if not words:
        return response_json.get("text", "")

    lines = []
    current_speaker = object()  # sentinel that won't equal any real speaker_id
    current_parts = []

    for word in words:
        speaker = word.get("speaker_id")
        text = word.get("text", "")

        if word.get("type") == "audio_event":
            # Non-speech events (laughter, etc.) stay inline with the
            # current speaker's turn rather than starting a new one.
            current_parts.append(text)
            continue

        if speaker != current_speaker:
            if current_parts:
                lines.append(f"{_speaker_label(current_speaker)}: {''.join(current_parts).strip()}")
            current_speaker = speaker
            current_parts = []

        current_parts.append(text)

    if current_parts:
        lines.append(f"{_speaker_label(current_speaker)}: {''.join(current_parts).strip()}")

    return "\n".join(lines) if lines else response_json.get("text", "")


class STT:

    def __init__(self):
        if not STT_API_KEY:
            raise ValueError("STT_API_KEY not found")

    def transcribe(self, s3_key: str, audio_path: str | None = None, max_retries: int = 3) -> str:
        print(f"[STT] Transcribing {s3_key}")
        s3 = S3Service()
        if audio_path is None:
            suffix = Path(str(s3_key).split("?", 1)[0]).suffix or ".wav"
            temporary = tempfile.NamedTemporaryFile(prefix="flintdesk-stt-", suffix=suffix, delete=False)
            audio_path = temporary.name
            temporary.close()
        else:
            temporary = None
        Path(audio_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            print(f"[STT] Downloading audio from S3: {s3_key}")
            s3.download_file(s3_key, audio_path)

            file_ext = Path(audio_path).suffix.lower()
            content_type_map = {
                ".wav": "audio/wav",
                ".mp3": "audio/mpeg",
                ".m4a": "audio/mp4",
                ".ogg": "audio/ogg",
            }
            content_type = content_type_map.get(file_ext, "audio/wav")
            print(f"[STT] Format: {file_ext} -> {content_type}")

            with open(audio_path, "rb") as audio_file:
                audio_data = audio_file.read()

            print(f"[STT] Sending {len(audio_data)} bytes to API")
            report_stt_audio_seconds(wav_duration_seconds(audio_data))

            last_error = None
            for attempt in range(max_retries):
                try:
                    form_data = [
                        ("model_id", STT_MODEL),
                        ("language_code", "en"),
                        ("diarize", "true"),
                        ("detect_speaker_roles", "true"),
                    ] + [("keyterms", term) for term in get_front_desk_keyterms()]

                    response = requests.post(
                        STT_BASE_URL,
                        headers={"xi-api-key": STT_API_KEY},
                        files={"file": (Path(audio_path).name, audio_data, content_type)},
                        data=form_data,
                        timeout=300,
                    )
                    print(f"[STT] Status: {response.status_code}")
                    if response.status_code == 429:
                        # ElevenLabs uses detail.status to distinguish concurrency
                        # limit hits (back off briefly, a slot should free up soon)
                        # from system-wide throttling (back off longer).
                        try:
                            detail_status = response.json().get("detail", {}).get("status", "")
                        except ValueError:
                            detail_status = ""
                        if detail_status == "too_many_concurrent_requests":
                            retry_after = int(response.headers.get("Retry-After", 3))
                            print(f"[STT] Concurrency limit hit ({detail_status}), waiting {retry_after}s...")
                        else:
                            retry_after = int(response.headers.get("Retry-After", 10))
                            print(f"[STT] Rate limited ({detail_status or 'unknown'}), waiting {retry_after}s...")
                        time.sleep(retry_after)
                        continue
                    if not response.ok:
                        # Include the provider's body (e.g. the 422 validation
                        # detail) so it reaches calls.error_message.
                        raise requests.exceptions.HTTPError(
                            f"{response.status_code} from STT API: {response.text[:300]}",
                            response=response,
                        )
                    response_json = response.json()
                    result = _build_labeled_transcript(response_json)
                    print(f"[STT] Success! {len(result)} chars")
                    return result
                except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError) as e:
                    last_error = e
                    wait = 2 ** attempt
                    print(f"[STT] Connection error (attempt {attempt+1}/{max_retries}), retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                except requests.exceptions.RequestException as e:
                    print(f"[STT] Error: {e}")
                    # Response.__bool__ is False for 4xx/5xx, so compare to None.
                    if getattr(e, 'response', None) is not None:
                        print(f"[STT] Response: {e.response.status_code} - {e.response.text[:200]}")
                    raise
            raise last_error or Exception("All STT retries failed")
        finally:
            if temporary is not None:
                Path(audio_path).unlink(missing_ok=True)
