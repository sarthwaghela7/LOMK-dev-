"""Synchronize September 2026 call durations from their S3 WAV audio.

Run from the backend root.  The default is a dry run; pass --apply to commit
updates.  A valid WAV header is the authoritative duration source; values are
updated only when they differ from that recording length.
"""

from __future__ import annotations

import argparse
from datetime import date

from storage.db import SessionLocal
from storage.models import Call, CallAnalysisResult
from services.Database_sync.f1 import F1Processor


START_DATE = date(2026, 9, 1)


def is_missing(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def display_duration(seconds: float) -> str:
    """Use whole recording seconds, matching the browser audio player's display."""
    return str(int(seconds))


def run_backfill(end_date: date, apply: bool = False) -> dict[str, int]:
    db = SessionLocal()
    summary = {
        "total_september_calls_examined": 0,
        "calls_with_missing_duration": 0,
        "calls_requiring_audio_duration_sync": 0,
        "audio_files_successfully_found": 0,
        "durations_successfully_calculated": 0,
        "database_records_successfully_updated": 0,
        "missing_audio_files": 0,
        "corrupted_or_unreadable_audio_files": 0,
        "other_errors": 0,
    }
    try:
        calls = (
            db.query(Call)
            .filter(Call.call_date >= START_DATE, Call.call_date <= end_date)
            .order_by(Call.call_date, Call.call_time)
            .all()
        )
        analyses = {
            str(row.call_id): row
            for row in db.query(CallAnalysisResult)
            .filter(CallAnalysisResult.call_id.in_([str(call.call_id) for call in calls]))
            .all()
        } if calls else {}
        probe = F1Processor()

        for call in calls:
            summary["total_september_calls_examined"] += 1
            analysis = analyses.get(str(call.call_id))
            if is_missing(call.billsec) or (analysis is not None and is_missing(analysis.call_duration)):
                summary["calls_with_missing_duration"] += 1
            key = call.audio_s3_key
            if not key and call.audio_s3_uri and call.audio_s3_uri.startswith("s3://"):
                key = call.audio_s3_uri.split("/", 3)[-1]
            if not key:
                summary["missing_audio_files"] += 1
                print(f"[missing audio] {call.call_reference or call.call_id}")
                continue

            try:
                probe.bucket = call.audio_s3_bucket or probe.bucket
                duration = probe._get_wav_duration_seconds(key)
            except Exception as exc:  # Keep one bad object from stopping the backfill.
                summary["other_errors"] += 1
                print(f"[audio error] {call.call_reference or call.call_id}: {exc}")
                continue
            if duration is None or duration <= 0:
                summary["corrupted_or_unreadable_audio_files"] += 1
                print(f"[unreadable audio] {call.call_reference or call.call_id}: {key}")
                continue

            summary["audio_files_successfully_found"] += 1
            summary["durations_successfully_calculated"] += 1
            actual_seconds = int(duration)
            actual_display = display_duration(duration)
            needs_call_duration = call.billsec != actual_seconds
            needs_analysis_duration = (
                analysis is not None
                and str(analysis.call_duration or "").strip() != actual_display
            )
            if not (needs_call_duration or needs_analysis_duration):
                continue

            summary["calls_requiring_audio_duration_sync"] += 1
            changes = []
            if needs_call_duration:
                call.billsec = actual_seconds
                changes.append("calls.billsec")
            if needs_analysis_duration:
                analysis.call_duration = actual_display
                changes.append("call_analysis_results.call_duration")
            if changes:
                summary["database_records_successfully_updated"] += 1
                print(f"[{'apply' if apply else 'dry-run'}] {call.call_reference or call.call_id}: "
                      f"{actual_display} seconds -> {', '.join(changes)}")

        if apply:
            db.commit()
        else:
            db.rollback()
        return summary
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--apply", action="store_true", help="Persist audio-duration synchronization updates.")
    args = parser.parse_args()
    result = run_backfill(args.end_date, apply=args.apply)
    print("\nSeptember audio-duration backfill summary:")
    for label, count in result.items():
        print(f"  {label.replace('_', ' ')}: {count}")
