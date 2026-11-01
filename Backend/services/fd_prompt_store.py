"""Read/write access to the versioned fd_prompts table.

Reads are cached for a short TTL so prompt edits reach every worker within
CACHE_TTL_SECONDS without a restart; a save on this process invalidates its own
cache immediately. A missing/invalid prompt is never replaced by a hardcoded
fallback: callers get None and must skip the automation (and the issue is logged).
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from storage.db import engine
from utils.ttl_cache import cache_get, cache_set

logger = logging.getLogger("flintdesk.fd_prompts")

CACHE_TTL_SECONDS = 60
_NAMESPACE = "fd_prompts"

STAGE2_PROMPT_KEYS = ("stage2_self_action_item", "action_due_date", "follow_up_date")
# Prompts for other AI features live in the same table but are not part of the Stage 2 set / Settings panel.
PRIORITY_CASE_PROMPT_KEY = "priority_case_classification"
MAX_PROMPT_CHARS = 20000
_COLUMNS = "prompt_key, prompt_name, system_prompt, user_prompt_template, version, is_active, updated_at, updated_by"


def _row(r):
    return {
        "prompt_key": r[0], "prompt_name": r[1], "system_prompt": r[2], "user_prompt_template": r[3],
        "version": r[4], "is_active": r[5],
        "updated_at": r[6].isoformat() if r[6] else None, "updated_by": r[7],
    }


def invalidate_prompt_cache():
    for key in (*STAGE2_PROMPT_KEYS, PRIORITY_CASE_PROMPT_KEY):
        cache_set(_NAMESPACE, (key,), None, ttl_seconds=0)


def validate_prompt_fields(system_prompt, user_prompt_template):
    """Return an error string, or None when both fields are usable."""
    for label, value in (("system_prompt", system_prompt), ("user_prompt_template", user_prompt_template)):
        if not isinstance(value, str) or not value.strip():
            return f"{label} is required."
        if len(value) > MAX_PROMPT_CHARS:
            return f"{label} must be at most {MAX_PROMPT_CHARS} characters."
    return None


def get_active_prompt(prompt_key):
    """Active prompt dict, or None when missing/invalid (logged)."""
    cached = cache_get(_NAMESPACE, (prompt_key,))
    if cached:
        return cached
    try:
        with engine.connect() as conn:
            r = conn.execute(
                text(f"SELECT {_COLUMNS} FROM fd_prompts WHERE prompt_key = :k AND is_active"), {"k": prompt_key}
            ).first()
    except Exception as exc:
        logger.error("fd_prompts read failed for %s: %s", prompt_key, type(exc).__name__)
        return None
    prompt = _row(r) if r else None
    if prompt is None or validate_prompt_fields(prompt["system_prompt"], prompt["user_prompt_template"]):
        logger.error("fd_prompts: no valid active prompt for key %s", prompt_key)
        return None
    cache_set(_NAMESPACE, (prompt_key,), prompt, ttl_seconds=CACHE_TTL_SECONDS)
    return prompt


def get_stage2_prompts():
    """All three active prompts keyed by prompt_key, or None if any is unavailable."""
    prompts = {key: get_active_prompt(key) for key in STAGE2_PROMPT_KEYS}
    return prompts if all(prompts.values()) else None


def list_prompts(prompt_key=None):
    sql = f"SELECT {_COLUMNS} FROM fd_prompts"
    params = {}
    if prompt_key:
        sql += " WHERE prompt_key = :k"
        params["k"] = prompt_key
    with engine.connect() as conn:
        rows = conn.execute(text(sql + " ORDER BY prompt_key, version DESC"), params).fetchall()
    return [_row(r) for r in rows]


def save_new_version(prompt_key, system_prompt, user_prompt_template, updated_by, prompt_name=None):
    """Insert the next version and make it the single active one (atomic)."""
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"fd_prompts:{prompt_key}"})
        current = conn.execute(
            text("SELECT max(version), max(prompt_name) FROM fd_prompts WHERE prompt_key = :k"), {"k": prompt_key}
        ).first()
        if not current or current[0] is None:
            return None
        version = current[0] + 1
        conn.execute(text("UPDATE fd_prompts SET is_active = false WHERE prompt_key = :k AND is_active"), {"k": prompt_key})
        conn.execute(text("""
            INSERT INTO fd_prompts (prompt_key, prompt_name, system_prompt, user_prompt_template,
                                    version, is_active, updated_at, updated_by)
            VALUES (:k, :name, :sp, :up, :v, true, :now, :by)
        """), {"k": prompt_key, "name": prompt_name or current[1], "sp": system_prompt.strip(),
               "up": user_prompt_template.strip(), "v": version, "now": datetime.now(timezone.utc), "by": updated_by})
    invalidate_prompt_cache()
    return version


def activate_version(prompt_key, version, updated_by):
    """Roll back (or forward) by re-activating an existing version. False if it does not exist."""
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"fd_prompts:{prompt_key}"})
        exists = conn.execute(
            text("SELECT 1 FROM fd_prompts WHERE prompt_key = :k AND version = :v"), {"k": prompt_key, "v": version}
        ).first()
        if not exists:
            return False
        conn.execute(text("UPDATE fd_prompts SET is_active = false WHERE prompt_key = :k AND is_active"), {"k": prompt_key})
        conn.execute(text("""UPDATE fd_prompts SET is_active = true, updated_at = :now, updated_by = :by
                             WHERE prompt_key = :k AND version = :v"""),
                     {"k": prompt_key, "v": version, "now": datetime.now(timezone.utc), "by": updated_by})
    invalidate_prompt_cache()
    return True
