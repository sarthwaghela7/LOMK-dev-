"""Recover one FlintDesk call date through F1 -> F2 -> F3.

This is a deliberately date-exact, operator-invoked recovery tool. It is for
calls whose metadata arrived before the S3 recording and therefore missed their
first F1 audio mapping attempt. It never selects calls from another date.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
from pathlib import Path
from datetime import date, datetime

from sqlalchemy import or_

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.Database_sync.f1 import F1Processor
from services.Database_sync.f2 import F2Processor
from services.Database_sync.f3 import F3Processor
from storage.db import SessionLocal
from storage.models import Call


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def split_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def scoped_calls(target_date: date, sources: list[str], destinations: list[str], extensions: list[str]):
    db = SessionLocal()
    try:
        return (
            db.query(Call)
            .filter(
                Call.call_date == target_date,
                Call.extension.in_(extensions),
                or_(Call.src.in_(sources), Call.dst.in_(destinations)),
            )
            .order_by(Call.call_time.asc())
            .all()
        )
    finally:
        db.close()


def process_date(
    target_date: date,
    sources: list[str],
    destinations: list[str],
    extensions: list[str],
    stage: str = "all",
    workers: int = 3,
) -> dict[str, int]:
    """Run F1 -> F2 -> F3 only for one exact, explicitly scoped call set."""
    print(f"[recovery] Starting scoped FlintDesk recovery for {target_date}")

    # F1 mapping only: do not fetch or insert Vitel metadata outside the
    # supplied database scope. Existing audio mappings are preserved.
    f1_records = [
        record
        for record in scoped_calls(target_date, sources, destinations, extensions)
        if not record.audio_s3_uri
    ]
    mappings = []
    if stage in {"all", "f1"}:
        f1 = F1Processor()
        audio_files = f1._get_latest_batch(target_date)
        mappings = f1._match_audio(f1_records, audio_files)
        if mappings:
            f1._update_audio_paths(mappings)
    print(
        f"[recovery] F1 candidates: {len(f1_records)}, "
        f"audio mappings saved: {len(mappings)}"
    )

    f2_records = [
        record
        for record in scoped_calls(target_date, sources, destinations, extensions)
        if record.audio_s3_uri
        and not (record.transcript or "").strip()
        and record.processing_status != "failed_permanent"
    ]
    f2_processed = 0
    if stage in {"all", "f2"}:
        f2 = F2Processor()
        try:
            print(f"[recovery] F2 candidates for {target_date}: {len(f2_records)}")
            with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as executor:
                f2_processed = sum(bool(result) for result in executor.map(f2.process_record, f2_records))
        finally:
            f2.close()

    f3_records = [
        record
        for record in scoped_calls(target_date, sources, destinations, extensions)
        if (record.transcript or "").strip()
        and record.fd_processing_status != "processed"
    ]
    f3_processed = 0
    if stage in {"all", "f3"}:
        f3 = F3Processor(application="flintdesk")
        try:
            print(f"[recovery] F3 candidates for {target_date}: {len(f3_records)}")
            with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as executor:
                futures = [executor.submit(f3.process_record, record) for record in f3_records]
                for future in as_completed(futures):
                    f3_processed += bool(future.result())
        finally:
            if f3.db is not None:
                f3.db.close()

    result = {
        "f1_candidates": len(f1_records),
        "f1_matched": len(mappings),
        "f2_candidates": len(f2_records),
        "f2_processed": f2_processed,
        "f3_candidates": len(f3_records),
        "f3_processed": f3_processed,
    }
    print(f"[recovery] Complete: {result}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run scoped F1 -> F2 -> FlintDesk F3 recovery.")
    parser.add_argument("--date", required=True, type=parse_date)
    parser.add_argument("--src", required=True, type=split_values)
    parser.add_argument("--dst", required=True, type=split_values)
    parser.add_argument("--extensions", required=True, type=split_values)
    parser.add_argument("--stage", choices=("all", "f1", "f2", "f3"), default="all")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    process_date(args.date, args.src, args.dst, args.extensions, args.stage, args.workers)
