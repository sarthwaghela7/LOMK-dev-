"""Shared rules for the Case Types + EQ trends: case-type categories and enquiry-question normalisation.

Source fields (call_analysis_results): case type = fd_a_case_type, falling back to case_type (free text written by the
AI); enquiry question = enquiry_details (the enquiry_question column is not populated). The original text is never
changed; these rules only decide which bucket a record is counted in.
"""
from __future__ import annotations

import re
import unicodedata

UNCLASSIFIED = "Unclassified"
OTHER = "Other case types"      # kept for older callers; classify_case_type no longer returns it (see dynamic_label)
MAX_LABEL = 60

# First match wins. H-4 is checked before H-1B so "H1B/H4 EAD" lands in H-4 EAD, as the existing page does.
CATEGORIES = (
    ("H-4 / H-4 EAD", r"\bh[\s-]?4(?!\d)"),
    ("H-1B", r"\bh[\s-]?1[\s-]?b"),
    ("F-1 / OPT / STEM", r"\bf[\s-]?1\b|\bopt\b|\bstem\b|\bcpt\b"),
    ("I-140 / EB / PERM", r"\bi[\s-]?140\b|\bi[\s-]?14a\b|\beb[\s-]?\d|\bperm\b|\bniw\b|employment[\s-]?based"),
    ("I-485 / Green Card", r"\bi[\s-]?485\b|green\s*card|adjustment of status|\bgc\b"),
    ("I-130 / Family", r"\bi[\s-]?130\b|family[\s-]?based|spouse|marriage"),
    ("Citizenship", r"citizenship|naturali[sz]ation|\bn[\s-]?400\b"),
    ("RFE / NOID", r"\brfe\b|\bnoid\b"),
    ("Asylum", r"asylum"),
    ("O-1", r"\bo[\s-]?1\b"),
    ("L-1", r"\bl[\s-]?1\b"),
    ("B-1 / B-2 / Visa stamping", r"\bb[\s-]?[12]\b|stamping|consular"),
)
_COMPILED = tuple((name, re.compile(pattern, re.I)) for name, pattern in CATEGORIES)

# Values that say nothing specific: counted, but only as Unclassified (never guessed into a case type).
_GENERIC = re.compile(
    r"^(n/?a|none|unknown|not (applicable|specified|provided|sure)|other|general( inquiry| enquiry)?|inquiry|enquiry|"
    r"visas?|consultation|paid consultation|technical support|legal( advice)?|"
    r"immigration( (consultation|inquiry|enquiry|matter|case|services?|law|question|help|docs|documents))?)$", re.I)

NON_ENQUIRY_CLIENT_TYPES = ("voicemail call", "spam / wrong call")


def dynamic_label(text: str) -> str:
    """A specific label no known family covers, shown as its own category (cleaned, never merged with another type).

    Whitespace is collapsed and edge punctuation trimmed; an all-lowercase label is title-cased so "tn visa" and
    "TN visa" read alike. Anything else keeps the AI's wording.
    """
    label = " ".join(str(text or "").split()).strip(" .,;:-")
    if label.islower():
        label = label.title()
    return label[:MAX_LABEL].rstrip() or UNCLASSIFIED


def classify_case_type(*raw_values) -> str:
    """Category for the first non-blank raw label: a known family, the label itself (specific but not in a known
    family) or UNCLASSIFIED (blank / generic). Genuinely different case types are never merged into one bucket."""
    for raw in raw_values:
        text = " ".join(str(raw or "").split())
        if not text:
            continue
        if _GENERIC.match(text):
            return UNCLASSIFIED
        for name, pattern in _COMPILED:
            if pattern.search(text):
                return name
        return dynamic_label(text)
    return UNCLASSIFIED


def is_enquiry_client_type(client_type) -> bool:
    return str(client_type or "").strip().lower() not in NON_ENQUIRY_CLIENT_TYPES


def clean_question(value) -> str:
    """The question as displayed: original wording, whitespace tidied only."""
    return " ".join(str(value or "").split())


def question_key(value) -> str:
    """Comparison key for 'same question': case, punctuation and spacing differences are ignored, wording is not.

    "Is my I-485 delayed?" and "is my I485 delayed" are the same; two questions that differ in any word are not.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"(?<=\w)[-‐‑–](?=\w)", "", text)      # I-485 == I485
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


_PII = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"), "[phone]"),
)


def redact_for_ai(text, limit=180) -> str:
    """Question text sent to the AI: emails and phone numbers removed, length capped."""
    out = clean_question(text)
    for pattern, token in _PII:
        out = pattern.sub(token, out)
    return out[:limit]
