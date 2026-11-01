"""Read side of Priority Cases: stored classifications for the call records the Calls page lists. No model request, ever.

Scope = exactly the canonical call records the Calls page shows for the dates (services.analysis_read_query.analysis_read,
call_source=True, target_page="calls": routing legs / missed-call numbers / unanswered inbound legs excluded, duplicate
legs of one call collapsed to one record). A call's duplicate legs (same date, time, caller and callee) are merged:
a form confirmed on any leg counts for the surviving record, and `duplicates_merged` is the number of other legs.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from sqlalchemy import text

from services.priority_cases import FORMS

ROW_LIMIT_PER_FORM = 1000
RESULT_STATUSES = ("confirmed", "uncertain")
MAX_TRANSCRIPT_CHARS = 20_000

_ROWS_SQL = text("""
    WITH canon AS (
        SELECT c.call_id, c.call_date, c.call_time, c.src, c.dst, c.client_id, c.call_reference, c.call_number, c.transcript
        FROM calls c WHERE c.call_id = ANY(CAST(:ids AS uuid[]))
    ), members AS (
        SELECT canon.call_id AS canon_id, m.call_id AS member_id
        FROM canon JOIN calls m
          ON m.call_date = canon.call_date AND m.call_time = canon.call_time
         AND btrim(coalesce(m.src, '')) = btrim(coalesce(canon.src, ''))
         AND btrim(coalesce(m.dst, '')) = btrim(coalesce(canon.dst, ''))
    )
    SELECT mem.canon_id::text AS canon_id, mem.member_id::text AS member_id,
           p.result, p.confirmed_forms, p.uncertain_forms, p.analysis_status
    FROM members mem JOIN call_priority_analysis p ON p.call_id = mem.member_id
    WHERE p.analysis_status = 'completed'
      AND (p.confirmed_forms && CAST(:forms AS text[]) OR p.uncertain_forms && CAST(:forms AS text[]))
""")

_DETAIL_SQL = text("""
    SELECT c.call_id::text AS call_id, c.call_date, c.call_time, c.src, c.dst, c.call_reference, c.call_number, c.transcript,
           coalesce(nullif(btrim(cl.client_name), ''), nullif(btrim(a.fd_m_client_name), ''), nullif(btrim(a.fd_a_client_name), '')) AS name,
           coalesce(nullif(btrim(cl.client_email), ''), nullif(btrim(a.fd_a_email_address), '')) AS email,
           coalesce(nullif(btrim(a.fd_a_case_type), ''), nullif(btrim(a.case_type), '')) AS case_type,
           coalesce(nullif(btrim(a.fd_a_call_summary), ''), nullif(btrim(a.call_summary), '')) AS call_summary,
           (SELECT count(*) FROM calls m WHERE m.call_date = c.call_date AND m.call_time = c.call_time
               AND btrim(coalesce(m.src, '')) = btrim(coalesce(c.src, ''))
               AND btrim(coalesce(m.dst, '')) = btrim(coalesce(c.dst, ''))) - 1 AS duplicates_merged
    FROM calls c
    LEFT JOIN clients cl ON cl.client_id = c.client_id
    LEFT JOIN call_analysis_results a ON a.call_id::text = c.call_id::text
    WHERE c.call_id = ANY(CAST(:ids AS uuid[]))
""")


def _remote_party(call, system_numbers, direction_of) -> str:
    """The client's number: the far end of the call. Blank when the direction cannot be established (never guessed)."""
    direction = direction_of(call, system_numbers)
    if direction == "Outbound":
        return str(call.dst or "").strip()
    if direction == "Inbound":
        return str(call.src or "").strip()
    return ""


def found_in_text(entry: dict) -> str:
    """The "<FORM> Found In" value: where the form was found (the transcript) and the passage that shows it."""
    evidence = (entry or {}).get("evidence")
    return f'Transcript: "{evidence}"' if evidence else ""


def _merge(entries: list[dict]) -> dict | None:
    """One form's classification across a record's legs: confirmed beats uncertain; the first leg with it supplies the evidence."""
    for wanted in RESULT_STATUSES:
        for entry in entries:
            if entry.get("status") == wanted:
                return entry
    return None


def priority_cases(db, *, start: date, end: date, form_types: tuple[str, ...] = FORMS, status: str = "confirmed",
                   limit: int = ROW_LIMIT_PER_FORM) -> dict:
    """{form: {"total": n, "truncated": bool, "rows": [...]}} for the requested forms/status, newest call first."""
    from routes.calls import _call_direction, _system_numbers
    from services.analysis_read_query import analysis_read

    scope = analysis_read(db, start=start, end=end, call_source=True, target_page="calls", ids_only=True)
    ids = [str(row[0]) for row in scope]
    out = {form: {"total": 0, "truncated": False, "rows": []} for form in form_types}
    if not ids:
        return out
    legs: dict[str, list[dict]] = {}
    for row in db.execute(_ROWS_SQL, {"ids": ids, "forms": list(form_types)}).mappings():
        legs.setdefault(row["canon_id"], []).append(row["result"] or {})
    if not legs:
        return out
    wanted_ids = list(legs)
    details = {row["call_id"]: row for row in db.execute(_DETAIL_SQL, {"ids": wanted_ids}).mappings()}
    system_numbers = _system_numbers(db)

    per_form: dict[str, list[tuple]] = {form: [] for form in form_types}
    for call_id, results in legs.items():
        detail = details.get(call_id)
        if detail is None:
            continue
        for form in form_types:
            entry = _merge([(result.get("forms") or {}).get(form) or {} for result in results])
            if entry and entry.get("status") == status:
                per_form[form].append((detail, entry))
    for form, items in per_form.items():
        items.sort(key=lambda pair: (pair[0]["call_date"], pair[0]["call_time"], pair[0]["call_id"]), reverse=True)
        out[form]["total"] = len(items)
        out[form]["truncated"] = len(items) > limit
        for detail, entry in items[:limit]:
            call = SimpleNamespace(src=detail["src"], dst=detail["dst"])
            out[form]["rows"].append({
                "call_id": detail["call_id"],
                "call_reference": detail["call_reference"] or (f"AF#{detail['call_number']}" if detail["call_number"] else None),
                "date": detail["call_date"].isoformat(),
                "time": detail["call_time"].strftime("%H:%M:%S") if detail["call_time"] else "",
                "name": detail["name"] or "",
                "email": detail["email"] or "",
                "phone": _remote_party(call, system_numbers, _call_direction),
                "case_type": detail["case_type"] or "",
                "call_summary": detail["call_summary"] or "",
                "transcript": (detail["transcript"] or "")[:MAX_TRANSCRIPT_CHARS],
                "found_in": found_in_text(entry),
                "evidence": entry.get("evidence"),
                "reason": entry.get("reason"),
                "confidence": entry.get("confidence"),
                "status": entry.get("status"),
                "duplicates_merged": int(detail["duplicates_merged"] or 0),
            })
    return out


def coverage_summary(db, *, start: date, end: date) -> dict:
    """How complete the stored analysis is for the Calls-page records in range (so reviewers can tell 'none found' from 'not analysed yet')."""
    from services.analysis_read_query import analysis_read

    ids = [str(row[0]) for row in analysis_read(db, start=start, end=end, call_source=True, target_page="calls", ids_only=True)]
    if not ids:
        return {"calls": 0, "with_transcript": 0, "analysed": 0, "pending": 0, "failed": 0, "uncertain": 0}
    row = db.execute(text("""
        SELECT count(*) AS calls,
               count(*) FILTER (WHERE coalesce(btrim(c.transcript), '') <> '') AS with_transcript,
               count(*) FILTER (WHERE p.analysis_status = 'completed') AS analysed,
               count(*) FILTER (WHERE coalesce(btrim(c.transcript), '') <> ''
                                  AND coalesce(p.analysis_status, 'pending') IN ('pending', 'processing')) AS pending,
               count(*) FILTER (WHERE p.analysis_status = 'failed') AS failed,
               count(*) FILTER (WHERE p.analysis_status = 'completed' AND cardinality(p.uncertain_forms) > 0) AS uncertain
        FROM calls c LEFT JOIN call_priority_analysis p ON p.call_id = c.call_id
        WHERE c.call_id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": ids}).first()
    return {key: int(value) for key, value in row._mapping.items()}
