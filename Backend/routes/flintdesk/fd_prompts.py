"""Admin API for the versioned fd_prompts used by FlintDesk Stage 2 AI automation.

Reads: settings viewers. Writes/rollback: FlintDesk admins only (enforced here,
not just hidden in the UI). Prompt text is AI configuration, so audit events
record who changed which key/version but not the prompt text itself.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from middleware.auth import require_fd_admin, require_fd_settings_view
from services.stage2_automation import audit_event as audit
from services.fd_prompt_store import (
    STAGE2_PROMPT_KEYS, activate_version, list_prompts, save_new_version, validate_prompt_fields,
)

router = APIRouter(prefix="/flintdesk/fd-prompts", tags=["FlintDesk Prompts"])


class PromptUpdate(BaseModel):
    system_prompt: str = Field(..., max_length=20000)
    user_prompt_template: str = Field(..., max_length=20000)


def _check_key(prompt_key: str):
    if prompt_key not in STAGE2_PROMPT_KEYS:
        raise HTTPException(status_code=404, detail="Unknown prompt key.")


def _actor(user) -> str:
    return str(user.get("name") or user.get("email") or user.get("sub") or "unknown")[:200]


@router.get("")
def get_prompts(include_history: bool = False, user=Depends(require_fd_settings_view)):
    # The table also holds prompts for other features (e.g. Priority Cases); this Settings panel is Stage 2 only.
    rows = [row for row in list_prompts() if row["prompt_key"] in STAGE2_PROMPT_KEYS]
    return {
        "keys": list(STAGE2_PROMPT_KEYS),
        "active": [row for row in rows if row["is_active"]],
        "history": rows if include_history else [],
    }


@router.put("/{prompt_key}")
def update_prompt(prompt_key: str, payload: PromptUpdate, user=Depends(require_fd_admin)):
    _check_key(prompt_key)
    error = validate_prompt_fields(payload.system_prompt, payload.user_prompt_template)
    if error:
        raise HTTPException(status_code=422, detail=error)
    version = save_new_version(prompt_key, payload.system_prompt, payload.user_prompt_template, _actor(user))
    if version is None:
        raise HTTPException(status_code=409, detail="Prompt has not been seeded; run migration 069.")
    audit(user, "FD_PROMPT_UPDATED", "fd_prompt", prompt_key, category="admin", metadata={"version": version})
    return {"prompt_key": prompt_key, "version": version}


@router.post("/{prompt_key}/rollback/{version}")
def rollback_prompt(prompt_key: str, version: int, user=Depends(require_fd_admin)):
    _check_key(prompt_key)
    if not activate_version(prompt_key, version, _actor(user)):
        raise HTTPException(status_code=404, detail="Prompt version not found.")
    audit(user, "FD_PROMPT_ROLLED_BACK", "fd_prompt", prompt_key, category="admin", metadata={"version": version})
    return {"prompt_key": prompt_key, "version": version}
