from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text

from storage.db import engine
from middleware.auth import get_current_user, require_role
from utils.prompt_config import reload_prompts

router = APIRouter(prefix="/prompts", tags=["Prompts"])


class PromptCreateRequest(BaseModel):
    key: str
    description: Optional[str] = None
    notes: Optional[str] = None


class PromptUpdateRequest(BaseModel):
    key: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None


@router.get("")
def list_prompts(user=Depends(get_current_user)):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, key, description, notes FROM prompts ORDER BY id")
        ).fetchall()
    return [
        {"id": row[0], "key": row[1], "description": row[2], "notes": row[3]}
        for row in rows
    ]


@router.post("")
def create_prompt(payload: PromptCreateRequest, user=Depends(require_role("superuser"))):
    key = (payload.key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Key is required.")

    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM prompts WHERE key = :key"),
            {"key": key}
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="A prompt with this key already exists.")

        result = conn.execute(
            text(
                "INSERT INTO prompts (key, description, notes) VALUES (:key, :description, :notes) RETURNING id"
            ),
            {"key": key, "description": payload.description, "notes": payload.notes},
        )
        new_id = result.fetchone()[0]

    reload_prompts()

    return {"id": new_id, "key": key, "description": payload.description, "notes": payload.notes}


@router.put("/{prompt_id}")
def update_prompt(prompt_id: int, payload: PromptUpdateRequest, user=Depends(require_role("superuser"))):
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id, key, description, notes FROM prompts WHERE id = :id"),
            {"id": prompt_id}
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Prompt not found.")

        new_key = (payload.key or existing[1]).strip()
        new_desc = payload.description if payload.description is not None else existing[2]
        new_notes = payload.notes if payload.notes is not None else existing[3]

        conn.execute(
            text("UPDATE prompts SET key = :key, description = :description, notes = :notes WHERE id = :id"),
            {"key": new_key, "description": new_desc, "notes": new_notes, "id": prompt_id},
        )

    reload_prompts()
    return {"id": prompt_id, "key": new_key, "description": new_desc, "notes": new_notes}


@router.delete("/{prompt_id}")
def delete_prompt(prompt_id: int, user=Depends(require_role("superuser"))):
    if prompt_id == 1:
        raise HTTPException(status_code=400, detail="Prompt ID 1 is reserved for system instructions and cannot be deleted.")

    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM prompts WHERE id = :id"),
            {"id": prompt_id}
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Prompt not found.")

        conn.execute(text("DELETE FROM prompts WHERE id = :id"), {"id": prompt_id})

    reload_prompts()
    return {"detail": "Deleted successfully."}
