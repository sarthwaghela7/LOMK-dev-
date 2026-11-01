"""Prompt configuration loaded from the database.

Row 1 is treated as the instruction block.
Rows 2+ are treated as analysis fields.
"""

from sqlalchemy import text

from storage.db import engine


def load_prompt_rows():
    """Return prompt rows ordered by id."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, key, description, notes FROM prompts ORDER BY id")
            ).fetchall()
    except Exception as exc:
        print(f"[prompt_config] Error loading prompts from DB: {exc}")
        return []

    result = []
    for prompt_id, key, description, notes in rows:
        result.append(
            {
                "id": prompt_id,
                "key": (key or "").strip(),
                "description": (description or "").strip(),
                "notes": (notes or "").strip(),
            }
        )
    return result


def get_analysis_fields():
    """Return prompt rows that represent output fields."""
    return [row for row in load_prompt_rows() if row["id"] != 1 and row["key"]]


def build_llm_system_prompt() -> str:
    """Build the system prompt from DB prompts."""
    rows = load_prompt_rows()
    if not rows:
        return ""

    parts = []

    instruction_row = rows[0] if rows[0]["id"] == 1 else None
    if instruction_row:
        instruction_parts = [instruction_row["key"]]
        if instruction_row["description"]:
            instruction_parts.append(instruction_row["description"])
        if instruction_row["notes"]:
            instruction_parts.append(instruction_row["notes"])
        parts.append("\n".join(part for part in instruction_parts if part))

    analysis_fields = get_analysis_fields()
    if analysis_fields:
        schema_lines = [
            "OUTPUT REQUIREMENTS:",
            "Return only valid JSON.",
            "Use these keys exactly for the analysis fields:",
        ]
        for field in analysis_fields:
            label = field["description"] or field["key"]
            schema_lines.append(f"- {field['key']}: {label}")
            if field["notes"]:
                schema_lines.append(f"  Notes: {field['notes']}")
        parts.append("\n".join(schema_lines))

    return "\n\n".join(part for part in parts if part)


def reload_prompts():
    """Re-build the prompt from the database (call after updating prompts table)."""
    global LLM_NODE_SYSTEM_PROMPT
    LLM_NODE_SYSTEM_PROMPT = build_llm_system_prompt()


LLM_NODE_SYSTEM_PROMPT = build_llm_system_prompt()



