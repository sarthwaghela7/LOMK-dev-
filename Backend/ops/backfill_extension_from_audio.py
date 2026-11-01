"""TEMPORARY one-off: restore the agent extension on outbound calls Vitel logged in the new format.

Since 2026-10-06 13:14 CT Vitel puts the dialed client number in the CDR
`extension` column.  F1 now corrects that when it links a recording; this
script does the same for calls that were already linked before that change
shipped.  Dry run by default (read-only); pass --apply to write.

    ./venv/bin/python ops/backfill_extension_from_audio.py --start 2026-10-06
    ./venv/bin/python ops/backfill_extension_from_audio.py --start 2026-10-06 --apply
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text

from services.Database_sync.f1 import F1Processor
from storage.db import SessionLocal
from storage.models import Call


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="first Central call date, YYYY-MM-DD")
    parser.add_argument("--end", help="last Central call date (default: start)")
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else start

    parse = F1Processor()._parse_audio_filename
    db = SessionLocal()
    try:
        if not args.apply:
            db.execute(text("SET TRANSACTION READ ONLY"))
        calls = (
            db.query(Call)
            .filter(Call.call_date >= start, Call.call_date <= end, Call.audio_s3_key.isnot(None))
            .order_by(Call.call_date, Call.call_time)
            .all()
        )
        changes = []
        for call in calls:
            audio = parse(call.audio_s3_key)
            extension = F1Processor.extension_from_audio(audio, call) if audio else None
            if extension:
                changes.append((call, extension))
        print(f"{'APPLY' if args.apply else 'DRY RUN'}: {len(changes)} of {len(calls)} linked call(s) "
              f"between {start} and {end} need the extension restored")
        for call, extension in changes:
            print(f"  {call.call_reference} {call.call_date} {call.call_time} {call.src} -> {call.dst}: "
                  f"extension {call.extension!r} -> {extension!r}")
            if args.apply:
                if str(call.extname or "").strip() == str(call.dst or "").strip():
                    call.extname = extension
                call.extension = extension
        if args.apply:
            db.commit()
            print("Committed.")
        else:
            db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
