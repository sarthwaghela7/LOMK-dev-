"""
One-off manual backfill: run FlintDesk F3 (LLM analysis) for 2026-09-10 calls
that already have a transcript but never got an AI analysis result.

Scope ((src OR dst) AND extension matches):
    src        13128781255, 12243374447
    dst        3128781255, 2243374447, 601, 603
    extension  101, 102, 201, 202

Usage:
    python run_manual_f3_backfill_20260910.py [--dry-run]
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from storage.db import SessionLocal
from storage.models import Call
from services.Database_sync.f3 import F3Processor
from utils.config import F3_LLM_WORKERS

TARGET_DATE = date(2026, 9, 10)
SRC_NUMBERS = {"13128781255", "12243374447"}
DST_NUMBERS = {"3128781255", "2243374447", "601", "603"}
EXTENSIONS = {"101", "102", "201", "202"}


def _normalize(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


NORMALIZED_SRC = {_normalize(v) for v in SRC_NUMBERS}
NORMALIZED_DST = {_normalize(v) for v in DST_NUMBERS}


def fetch_target_calls():
    db = SessionLocal()
    try:
        candidates = (
            db.query(Call)
            .filter(Call.call_date == TARGET_DATE)
            .filter(Call.transcript.isnot(None), Call.transcript != "")
            .all()
        )
        matched = [
            call for call in candidates
            if (
                _normalize(call.src) in NORMALIZED_SRC
                or _normalize(call.dst) in NORMALIZED_DST
            )
            and str(call.extension or "") in EXTENSIONS
        ]
        # Only backfill calls FlintDesk hasn't already analyzed.
        pending = [call for call in matched if call.fd_processing_status != "processed"]
        for call in pending:
            db.expunge(call)
        return pending
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N calls (for test batches)")
    args = parser.parse_args()

    calls = fetch_target_calls()
    print(f"Found {len(calls)} calls on {TARGET_DATE} matching scope with missing FlintDesk analysis")
    for call in calls:
        print(f"  {call.call_id} src={call.src} dst={call.dst} ext={call.extension}")

    if args.limit is not None:
        calls = calls[: args.limit]
        print(f"Limiting to first {len(calls)} call(s) for this run")

    if args.dry_run or not calls:
        return

    f3 = F3Processor(application="flintdesk")

    # Claim rows first so this one-off script can never double-process a call
    # that the live per-minute F3 scheduler job (or its fallback) is also
    # working on right now.
    won_ids = set(f3.claim_calls([call.call_id for call in calls]))
    skipped = len(calls) - len(won_ids)
    if skipped:
        print(f"{skipped} call(s) already claimed by the live pipeline, skipping")
    calls = [call for call in calls if call.call_id in won_ids]
    if not calls:
        print("Nothing left to backfill after claiming.")
        return

    worker_count = max(1, min(F3_LLM_WORKERS, len(calls)))

    results = []
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="f3-backfill") as executor:
        futures = [executor.submit(f3.process_record, call) for call in calls]
        for future in as_completed(futures):
            results.append(future.result())

    processed = sum(r is True for r in results)
    failed = sum(r is False for r in results)
    print(f"Backfill complete: processed={processed} failed={failed}")


if __name__ == "__main__":
    sys.exit(main())
