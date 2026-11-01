"""TEMPORARY one-off: apply the content-based voicemail rule to existing analysis rows.

Writes voicemail_left_if_no_answer only where it changes the outcome (see
services.voicemail_content.desired_voicemail_flag); an explicit Yes/No already on
a row is never changed. Dry run by default (read-only); pass --apply to write.

    ./venv/bin/python ops/backfill_voicemail_from_content.py --start 2026-10-01 --end 2026-10-08
    ./venv/bin/python ops/backfill_voicemail_from_content.py --start 2026-10-01 --end 2026-10-08 --apply
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import cast, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from services.voicemail_content import desired_voicemail_flag
from storage.db import SessionLocal
from storage.models import Call, CallAnalysisResult


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="first call date, YYYY-MM-DD")
    parser.add_argument("--end", help="last call date (default: start)")
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else start

    db = SessionLocal()
    try:
        if not args.apply:
            db.execute(text("SET TRANSACTION READ ONLY"))
        pairs = (
            db.query(Call, CallAnalysisResult)
            .join(CallAnalysisResult, Call.call_id == cast(CallAnalysisResult.call_id, PGUUID))
            .filter(Call.call_date >= start, Call.call_date <= end, Call.transcript.isnot(None))
            .order_by(Call.call_date, Call.call_time)
            .all()
        )
        changes = []
        for call, analysis in pairs:
            value = desired_voicemail_flag(analysis.voicemail_left_if_no_answer, call.transcript, analysis.fd_a_call_outcome)
            if value is not None:
                changes.append((call, analysis, value))
        print(f"{'APPLY' if args.apply else 'DRY RUN'}: {len(changes)} of {len(pairs)} analysed call(s) "
              f"between {start} and {end} change voicemail status")
        for call, analysis, value in changes:
            print(f"  {call.call_reference} {call.call_date} {call.call_time} billsec={call.billsec} "
                  f"flag {analysis.voicemail_left_if_no_answer!r} -> {value!r} | outcome: {str(analysis.fd_a_call_outcome)[:60]}")
            if args.apply:
                analysis.voicemail_left_if_no_answer = value
        if args.apply:
            db.commit()
            print("Committed.")
        else:
            db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
