"""Follow Up Dates (call_analysis_results.fd_m_follow_up_dates) filtering helpers.

The column is free TEXT: one entry per line (agents add several), written by the
AI/agents as MM-DD-YYYY, with legacy values such as "N/A", "2" or "NA" that are not
dates. So dates are matched as exact calendar-date strings and parsed strictly in
Python; nothing is ever cast to DATE in SQL (a bad value would raise). No timezone
is involved anywhere: a follow-up date is a calendar date.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import Text, cast, func
from sqlalchemy.dialects.postgresql import ARRAY, array

from storage.models import CallAnalysisResult as Analysis

_ENTRY_SPLIT = r"[\r\n]+"
_STORED_FORMATS = ("%m-%d-%Y", "%m/%d/%Y", "%Y-%m-%d")
# Entries that look like a date in one of the stored formats; stricter parsing is done in Python.
DATE_LIKE_REGEX = r"^([0-9]{2}[-/][0-9]{2}[-/][0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})$"


def parse_follow_up_date_param(value: str | None) -> date | None:
    """Validate the ?follow_up_date=YYYY-MM-DD query value (422 on anything else)."""
    if value is None or not str(value).strip():
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="follow_up_date must be a valid date in YYYY-MM-DD format.") from None


def stored_forms(day: date) -> list[str]:
    """Every textual form under which `day` can be stored in the column."""
    return [day.strftime(fmt) for fmt in _STORED_FORMATS]


def _entries_array():
    """SQL: the column's entries as a text[] (whitespace removed, split on newlines)."""
    cleaned = func.regexp_replace(func.coalesce(Analysis.fd_m_follow_up_dates, ""), r"[ \t]+", "", "g")
    return func.regexp_split_to_array(cleaned, _ENTRY_SPLIT)


def has_follow_up_date(day: date):
    """SQL predicate: any entry of the column equals `day` (parameterised, exact)."""
    return _entries_array().op("&&")(cast(array(stored_forms(day)), ARRAY(Text)))


def date_like_entries():
    """SQL: set of the column's entries that look like a date."""
    return func.unnest(_entries_array())


def parse_entries(raw_entries) -> list[date]:
    """Distinct, valid calendar dates (ascending) from raw entries; everything else is ignored."""
    found = set()
    for raw in raw_entries:
        text_value = str(raw or "").strip()
        for fmt in _STORED_FORMATS:
            try:
                found.add(datetime.strptime(text_value, fmt).date())
                break
            except ValueError:
                continue
    return sorted(found)
