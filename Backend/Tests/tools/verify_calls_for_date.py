"""Read-only reconciliation of Vitel CDR data against processing records.

Usage:
    python -m Tests.tools.verify_calls_for_date --date 2026-08-17
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

import pytz
from sqlalchemy import or_, text
from sqlalchemy.exc import DBAPIError, OperationalError

from services.Database_sync.f1 import F1Processor
from storage.db import SessionLocal
from storage.models import Call, CallAnalysisResult


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def call_key(call_date, call_time, src, dst, billsec) -> tuple:
    """Match F1's existing duplicate key exactly; the API ID is not persisted."""
    return (call_date, call_time, src, dst, int(billsec or 0))


def format_key(key: tuple) -> str:
    call_date, call_time, src, dst, billsec = key
    return f"{call_date} {call_time} | {src} -> {dst} | billsec={billsec}"


def analysis_date_variants(target_date: date) -> list[str]:
    return [
        target_date.strftime("%m/%d/%Y"),
        f"{target_date.month}/{target_date.day}/{target_date.year}",
        target_date.isoformat(),
    ]


def print_ids(title: str, identifiers: list[str]) -> None:
    print(f"\n{title}:")
    if identifiers:
        for identifier in identifiers:
            print(f"- {identifier}")
    else:
        print("- None")


def print_database_unavailable_report(target_date, central_start, central_end, raw_api_calls, eligible_api_calls, api_keys) -> None:
    print("=" * 50)
    print(f"CALL VERIFICATION - {target_date.isoformat()} CENTRAL TIME")
    print("=" * 50)
    print(f"Central business range: {central_start.isoformat()} through {central_end.isoformat()} (exclusive)")
    print(
        "Vitel request range: "
        f"startdate={target_date.isoformat()}, enddate={(target_date + timedelta(days=1)).isoformat()} (exclusive end date)"
    )
    print(f"Database condition: calls.call_date = {target_date.isoformat()}")
    print("\nExternal API:")
    print(f"Total calls returned: {len(raw_api_calls)}")
    print(f"F1-eligible calls: {len(eligible_api_calls)}")
    print(f"F1 leg-merged expected calls: {len(api_keys)}")
    print("\nDatabase calls and call_analysis_results:")
    print("Unavailable: the configured database connection timed out before any read query ran.")
    print("\nCross-check:")
    print("Unavailable until the database connection is restored.")
    print("\nSUMMARY")
    print(f"API raw: {len(raw_api_calls)}")
    print(f"F1 expected: {len(api_keys)}")
    print("calls: unavailable")
    print("transcripts: unavailable")
    print("analysis: unavailable")


def verify_calls(target_date: date) -> None:
    central = pytz.timezone("America/Chicago")
    central_start = central.localize(datetime.combine(target_date, datetime.min.time()))
    central_end = central_start + timedelta(days=1)

    # Reuse F1's production client, credentials, calendar-date request, filter,
    # and duplicate-leg merge. None of these operations writes to the database.
    f1 = F1Processor()
    db = SessionLocal()
    try:
        api_error = None
        try:
            raw_api_calls = f1._fetch_calls_for_date(target_date)
            eligible_api_calls = f1._filter_calls(raw_api_calls)
            merged_api_calls = f1._merge_call_legs(eligible_api_calls)
            api_keys = {
                call_key(
                    parse_date(record["call_date"]),
                    datetime.strptime(record["call_time"], "%H:%M:%S").time(),
                    record.get("src"),
                    record.get("dst"),
                    record.get("billsec", 0),
                )
                for record in merged_api_calls
            }
        except Exception as exc:
            raw_api_calls = []
            eligible_api_calls = []
            api_keys = set()
            api_error = type(exc).__name__

        try:
            db.execute(text("SELECT 1"))
        except (DBAPIError, OperationalError):
            if api_error:
                print(f"External API unavailable: {api_error}")
                print("Database unavailable: the configured connection timed out before any read query ran.")
            else:
                print_database_unavailable_report(
                    target_date,
                    central_start,
                    central_end,
                    raw_api_calls,
                    eligible_api_calls,
                    api_keys,
                )
            return

        calls = db.query(Call).filter(Call.call_date == target_date).all()
        calls_by_key = {
            call_key(call.call_date, call.call_time, call.src, call.dst, call.billsec): call
            for call in calls
        }
        call_ids = {str(call.call_id) for call in calls}
        transcript_calls = [call for call in calls if (call.transcript or "").strip()]
        transcript_ids = {str(call.call_id) for call in transcript_calls}

        analysis_scope = (
            db.query(CallAnalysisResult)
            .filter(
                or_(
                    CallAnalysisResult.call_id.in_(call_ids) if call_ids else False,
                    CallAnalysisResult.call_recording_date_normalized == target_date,
                    CallAnalysisResult.call_recording_date.in_(analysis_date_variants(target_date)),
                )
            )
            .all()
        )
        linked_analysis = [row for row in analysis_scope if str(row.call_id) in call_ids]
        analysis_ids = {str(row.call_id) for row in linked_analysis}
        completed = sum((row.processing_status or "").lower() == "completed" for row in linked_analysis)
        failed = sum((row.processing_status or "").lower() == "failed" for row in linked_analysis)

        api_missing_from_calls = sorted(api_keys - set(calls_by_key)) if api_error is None else []
        transcript_missing_analysis = sorted(transcript_ids - analysis_ids)
        analysis_without_calls = sorted(
            str(row.call_id) for row in analysis_scope if str(row.call_id) not in call_ids
        )

        print("=" * 50)
        print(f"CALL VERIFICATION - {target_date.isoformat()} CENTRAL TIME")
        print("=" * 50)
        print(f"Central business range: {central_start.isoformat()} through {central_end.isoformat()} (exclusive)")
        print(
            "Vitel request range: "
            f"startdate={target_date.isoformat()}, enddate={(target_date + timedelta(days=1)).isoformat()} (exclusive end date)"
        )
        print(f"Database condition: calls.call_date = {target_date.isoformat()}")
        print("\nExternal API:")
        if api_error:
            print(f"Unavailable: production client raised {api_error}; no API count was inferred.")
        else:
            print(f"Total calls returned: {len(raw_api_calls)}")
            print(f"F1-eligible calls: {len(eligible_api_calls)}")
            print(f"F1 leg-merged expected calls: {len(api_keys)}")
        print("\nDatabase calls:")
        print(f"Total calls: {len(calls)}")
        print(f"Calls with transcript: {len(transcript_calls)}")
        print(f"Calls without transcript: {len(calls) - len(transcript_calls)}")
        print("\nDatabase call_analysis_results:")
        print(f"Total analysis records linked to date's calls: {len(linked_analysis)}")
        print(f"Completed: {completed}")
        print(f"Failed: {failed}")
        print("\nCross-check:")
        if api_error:
            print("API calls missing from calls: unavailable because the API request did not complete")
        else:
            print(f"API calls missing from calls: {len(api_missing_from_calls)}")
        print(f"Calls with transcript missing from analysis: {len(transcript_missing_analysis)}")
        print(f"Analysis records with no matching call: {len(analysis_without_calls)}")
        print("\nMISSING CALL IDS")
        print_ids("API -> calls (F1 identity; API UUID is not persisted)", [format_key(key) for key in api_missing_from_calls])
        print_ids("calls transcript -> analysis (calls.call_id)", transcript_missing_analysis)
        print_ids("analysis -> calls (call_analysis_results.call_id)", analysis_without_calls)
        print("\nSUMMARY")
        print(f"API raw: {'unavailable' if api_error else len(raw_api_calls)}")
        print(f"F1 expected: {'unavailable' if api_error else len(api_keys)}")
        print(f"calls: {len(calls)}")
        print(f"transcripts: {len(transcript_calls)}")
        print(f"analysis: {len(linked_analysis)}")
    finally:
        db.rollback()
        db.close()
        f1.db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only Vitel/calls/analysis reconciliation")
    parser.add_argument("--date", required=True, type=parse_date, help="Central business date, YYYY-MM-DD")
    verify_calls(parser.parse_args().date)