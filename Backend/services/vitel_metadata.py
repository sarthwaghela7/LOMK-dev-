"""Interpretation of Vitel CDR metadata (disposition, recording filename, agent).

Vitel now reports ``disposition`` and ``callrecordfilename`` directly, so these
are treated as authoritative instead of being inferred from audio or an LLM.
Everything here is pure (no database or network access) so the Calls /
Missed Calls / audio rules can be unit tested and shared by F1, F3 and the
read paths.

Generic terms are used throughout: CLIENT_NUMBER, LEAD_NUMBER, AGENT_EXTENSION,
AI_ROUTE.  No customer data belongs in this module.
"""

from __future__ import annotations

import os
import re

from services.hannah_ai_routing import (
    FRONT_DESK_HANNAH_DST,
    FRONT_DESK_ROUTING_DST,
    HANNAH_AI_NUMBER,
    WEBSITE_HANNAH_DST,
    WEBSITE_ROUTING_DST,
    normalize_routing_number,
)

# Ring-route destinations: the internal group-ring legs a call passes through
# before an agent answers (or before it falls through to the AI route).
RING_ROUTE_DSTS = frozenset({FRONT_DESK_ROUTING_DST, WEBSITE_ROUTING_DST, "605"})

# The only AI/missed-call route destinations for the Missed Calls workflow.
AI_ROUTE_DSTS = frozenset({FRONT_DESK_HANNAH_DST, WEBSITE_HANNAH_DST})

# Values an inbound ``extension`` can hold that are NOT an answering agent.
NON_AGENT_EXTENSIONS = RING_ROUTE_DSTS | AI_ROUTE_DSTS | {HANNAH_AI_NUMBER}

ANSWERED = "ANSWERED"
VOICEMAIL = "VOICEMAIL"
# Dispositions of a leg nobody spoke on.
UNANSWERED = frozenset({"NOANSWER", "BUSY", "FAILED"})


def disposition_key(value) -> str:
    """Canonical disposition: upper-case, no spaces/underscores/hyphens.

    Vitel sends ``ANSWERED``, ``NO ANSWER``, ``BUSY``, ``FAILED`` and
    ``VoiceMail`` (mixed case); all compare equal to their canonical form.
    """
    return re.sub(r"[\s_\-]+", "", str(value or "")).upper()


def is_answered(disposition) -> bool:
    return disposition_key(disposition) == ANSWERED


def is_voicemail_disposition(disposition) -> bool:
    """Metadata-confirmed voicemail: Vitel's own ``VOICEMAIL`` disposition."""
    return disposition_key(disposition) == VOICEMAIL


def is_unanswered_disposition(disposition) -> bool:
    return disposition_key(disposition) in UNANSWERED


def is_ring_route_dst(dst) -> bool:
    return normalize_routing_number(dst) in RING_ROUTE_DSTS


def is_failed_ring_route_leg(call) -> bool:
    """A ring-route leg nobody answered (NO ANSWER / BUSY / FAILED).

    Such a leg is an internal routing attempt, never a call of its own: it is
    hidden from the Calls page whether or not the interaction later reached
    the AI route (in which case the interaction lives on Missed Calls).
    (The Calls-page query also hides every other unanswered INBOUND leg; this
    helper additionally covers ring routes and recording ownership.)
    """
    return is_ring_route_dst(getattr(call, "dst", None)) and is_unanswered_disposition(
        getattr(call, "disposition", None)
    )


def failed_ring_route_leg_ids(calls) -> set:
    """call_ids of the failed ring-route legs among ``calls``."""
    return {call.call_id for call in calls if is_failed_ring_route_leg(call)}


def inbound_agent_extension(call) -> str:
    """The answering agent extension of an ANSWERED inbound call, else "".

    Vitel reports the answering extension on inbound records.  It is an agent
    extension only when the leg was ANSWERED and:
      - the destination is a ring route (601/603/605): any extension except the
        AI attendant / route numbers.  This includes a line's own number used
        as an extension (e.g. the website line's 10-digit extension, which has
        no leading 1 while src/dst carry one); or
      - otherwise a plain 3-digit extension (direct-line records carry a phone
        number there, which is not an agent).
    The result is resolved by the SAME extension -> agent assignment lookup as
    outbound calls.
    """
    if not is_answered(getattr(call, "disposition", None)):
        return ""
    extension = str(getattr(call, "extension", "") or "").strip()
    if not extension or extension in NON_AGENT_EXTENSIONS:
        return ""
    if is_ring_route_dst(getattr(call, "dst", None)):
        if normalize_routing_number(extension) == normalize_routing_number(getattr(call, "src", None)):
            return ""  # caller's own number, not an agent
        return extension if re.fullmatch(r"\d{3}|\d{10}", extension) else ""
    return extension if re.fullmatch(r"\d{3}", extension) else ""


def clean_recording_filename(value) -> str | None:
    """Vitel ``callrecordfilename`` as a bare filename, or None when absent."""
    text = str(value or "").strip()
    if not text:
        return None
    name = os.path.basename(text.replace("\\", "/")).strip()
    return name or None


def _recording_key(filename) -> str:
    return str(filename or "").strip().lower()


def eligible_for_recording(call) -> bool:
    """Whether ``call`` may own the recording named in its metadata.

    A failed ring-route leg never owns it: Vitel stamps the same filename on
    every leg of one interaction, and for a ring-route fall-through that file
    is the AI route's recording, which must not be attached to an agent leg.
    """
    return not is_failed_ring_route_leg(call)


def _owner_rank(call):
    disposition = disposition_key(getattr(call, "disposition", None))
    tier = 0 if disposition == ANSWERED else 1 if disposition == VOICEMAIL else 2
    return (
        tier,
        -int(getattr(call, "billsec", 0) or 0),
        str(getattr(call, "call_time", "") or ""),
        str(getattr(call, "call_id", "") or ""),
    )


def recording_owner_ids(calls) -> set:
    """call_ids that own their Vitel recording filename.

    Every leg of one interaction carries the same ``callrecordfilename``, but
    the recording belongs to exactly one call row.  Per filename the owner is
    the leg that actually carried the conversation: ANSWERED first, then
    VOICEMAIL, then anything else (a lone unanswered outbound attempt keeps
    its own recording, as before).  Failed ring-route legs never own it.
    Deterministic: ties break on duration, time, then call_id.
    """
    groups: dict[str, list] = {}
    for call in calls:
        key = _recording_key(getattr(call, "vitel_recording_filename", None))
        if key and eligible_for_recording(call):
            groups.setdefault(key, []).append(call)
    return {min(group, key=_owner_rank).call_id for group in groups.values()}


def apply_metadata_voicemail(parsed: dict, call) -> dict:
    """Force the Voicemail Call classification when Vitel says VOICEMAIL.

    The AI's own voicemail verdict is not consulted for these records; every
    other analysed field is left as the AI produced it.  Non-voicemail records
    are returned untouched (existing Normal Call logic).
    """
    if not is_voicemail_disposition(getattr(call, "disposition", None)):
        return parsed
    result = dict(parsed)
    result["fd_a_client_type"] = "Voicemail Call"
    # Existing voicemail readers key on "voicemail" appearing in the outcome.
    if "voicemail" not in str(result.get("fd_a_call_outcome") or "").lower():
        result["fd_a_call_outcome"] = "Voicemail"
    return result
