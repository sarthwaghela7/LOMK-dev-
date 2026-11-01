"""Supabase Storage client for call/case attachments (private bucket).

Uses the service-role key from the backend environment only; this key must
never be sent to the frontend. Files are stored under
"{bucket}/{call_id}/{uuid}{ext}" and served back to the frontend via
short-lived signed URLs (see get_signed_url), never as public links.
"""
import requests

from utils.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_STORAGE_BUCKET


class SupabaseStorageError(Exception):
    pass


class SupabaseStorageService:
    def __init__(self, bucket: str | None = None):
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            raise SupabaseStorageError("Supabase Storage is not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing).")
        self.base_url = SUPABASE_URL.rstrip("/")
        self.bucket = bucket or SUPABASE_STORAGE_BUCKET
        self._headers = {
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
        }

    def ensure_bucket_exists(self):
        """Idempotent: creates the private bucket if it doesn't already exist."""
        response = requests.post(
            f"{self.base_url}/storage/v1/bucket",
            headers={**self._headers, "Content-Type": "application/json"},
            json={"id": self.bucket, "name": self.bucket, "public": False},
            timeout=15,
        )
        if response.status_code not in (200, 201) and "already exists" not in response.text.lower() and "duplicate" not in response.text.lower():
            raise SupabaseStorageError(f"Failed to ensure Supabase Storage bucket: {response.status_code} {response.text}")

    def upload_bytes(self, path: str, content: bytes, content_type: str) -> str:
        response = requests.post(
            f"{self.base_url}/storage/v1/object/{self.bucket}/{path}",
            headers={**self._headers, "Content-Type": content_type},
            data=content,
            timeout=30,
        )
        if response.status_code not in (200, 201):
            raise SupabaseStorageError(f"Supabase Storage upload failed: {response.status_code} {response.text}")
        return path

    def delete(self, path: str):
        if not path:
            return
        response = requests.delete(
            f"{self.base_url}/storage/v1/object/{self.bucket}",
            headers={**self._headers, "Content-Type": "application/json"},
            json={"prefixes": [path]},
            timeout=15,
        )
        # Deleting a file that no longer exists shouldn't fail the request.
        if response.status_code not in (200, 404):
            raise SupabaseStorageError(f"Supabase Storage delete failed: {response.status_code} {response.text}")

    def create_signed_url(self, path: str, expires_in: int = 3600) -> str:
        response = requests.post(
            f"{self.base_url}/storage/v1/object/sign/{self.bucket}/{path}",
            headers={**self._headers, "Content-Type": "application/json"},
            json={"expiresIn": expires_in},
            timeout=15,
        )
        if response.status_code != 200:
            raise SupabaseStorageError(f"Supabase Storage sign failed: {response.status_code} {response.text}")
        signed_url = response.json().get("signedURL")
        if not signed_url:
            raise SupabaseStorageError("Supabase Storage did not return a signed URL.")
        return f"{self.base_url}/storage/v1{signed_url}"
