"""Guard for human-entered values on call_analysis_results.

Background jobs (F2 failure placeholders, F3 AutoFlint analysis) write the
model's output onto an analysis row that an agent may already have edited.
Without a guard the job replaces what the agent typed -- e.g. the agent name
on an inbound call -- with the model's answer (often None or "N/A").
"""

# Placeholder values that carry no information and may be replaced.
_PLACEHOLDERS = {"", "-", "n/a", "na", "none", "unassigned"}


def is_manual_field(key: str) -> bool:
    """True for columns a person fills in: the agent name and Stage 2-4 (fd_m_*) fields."""
    return key in {"front_desk_person_name"} or key.startswith("fd_m_")


def has_manual_value(value) -> bool:
    """True when `value` is a real, human-entered value worth protecting."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().casefold() not in _PLACEHOLDERS
    return True


def apply_model_fields(analysis, values: dict) -> None:
    """Set model-produced `values` on `analysis`, never overwriting a manual value."""
    for key, value in values.items():
        if is_manual_field(key) and has_manual_value(getattr(analysis, key, None)):
            continue
        setattr(analysis, key, value)
