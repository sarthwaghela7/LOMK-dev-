"""Content-based voicemail detection (TEMPORARY, additive).

The existing rule is unchanged: a call is a voicemail when the AI flag says so
or its outcome text mentions voicemail. This module ADDS calls that the
transcript shows were voicemails but the AI missed (for example a call-screening
prompt that never put the caller through). It only ever sets the flag to "Yes";
it never removes a voicemail. It reads what was said, never the call length.

A transcript is a voicemail when the call opens with a voicemail-box greeting and
nobody really conversed (one side spoke at most twice), or when it opens with a
call-screening prompt and goes no further (the person was never put through).
A screened call that DOES connect continues with the person speaking, so it is
left alone. Everything else is left to the existing rule, exactly as before.
"""
import logging
import re

logger = logging.getLogger(__name__)

# Opening prompt of a voicemail box.
_BOX_GREETING = re.compile(
    r"(leave (?:me |us |your )?(?:a )?(?:message|voicemail)|after the tone|at the tone|mailbox|voicemail|voice mail|"
    r"can.t take your call|cannot take your call|please leave|reached the voice|is not accepting)"
)
# Opening prompt of a call-screening service ("record your name and reason for calling ...").
_SCREENING = re.compile(r"(record your name and reason for calling|see if this person is available)")
# A screened call that was NOT put through stops at the hold music / "not available",
# so there is almost nothing in the transcript after the opening prompt.
_SCREENED_MAX_CHARS = 320
_BLANK_FLAGS = {"", "-", "n/a", "na", "none", "not applicable", "unassigned"}


def turn_counts(transcript: str) -> tuple[int, int]:
    """(Client turns, Front Desk turns) from the speaker labels in the transcript."""
    text = transcript or ""
    return text.count("Client:"), text.count("Front Desk:")


def content_verdict(transcript: str):
    """"yes" (voicemail / not reached) or None (leave it to the existing rule)."""
    if not transcript or not transcript.strip():
        return None
    text = transcript.strip()
    head = text[:220].lower()
    quieter_side = min(turn_counts(text))
    if _BOX_GREETING.search(head) and quieter_side <= 2:
        return "yes"
    if _SCREENING.search(head) and len(text) <= _SCREENED_MAX_CHARS:
        return "yes"
    return None


def desired_voicemail_flag(current_flag, transcript: str, outcome: str = ""):
    """"Yes" to store in voicemail_left_if_no_answer, or None for no change.

    Only fills a blank flag: an explicit Yes/No already on the row (the AI's
    answer or an agent's correction) is never changed. Nothing is written when
    the outcome text already makes the call a voicemail, as it does today.
    """
    if str(current_flag or "").strip().lower() not in _BLANK_FLAGS:
        return None
    if "voicemail" in str(outcome or "").lower():
        return None
    return "Yes" if content_verdict(transcript) == "yes" else None


def apply_content_voicemail_flag(analysis, transcript: str) -> None:
    """Pipeline hook: run after the model's fields are saved on an analysis row.

    Best effort: this only adds a classification hint, so a problem here must
    never stop the analysis from being saved.
    """
    try:
        value = desired_voicemail_flag(
            getattr(analysis, "voicemail_left_if_no_answer", None),
            transcript,
            getattr(analysis, "fd_a_call_outcome", None),
        )
        if value is not None:
            analysis.voicemail_left_if_no_answer = value
    except Exception:  # noqa: BLE001
        logger.warning("Content-based voicemail check failed; leaving the AI's answer unchanged", exc_info=True)
