"""Delete calls and related analysis rows for a specific date.

Default target date: 7/7/2026.

Usage:
    python Tests/tools/delete_calls_by_date.py
    python Tests/tools/delete_calls_by_date.py --date 7/7/2026 --yes
    python Tests/tools/delete_calls_by_date.py --date 2026-07-07 --yes
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import or_

from storage.db import SessionLocal
from storage.models import Call, CallAnalysisResult


DEFAULT_DATE = "7/7/2026"


def parse_date(value: str) -> date:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value!r}. Use MM/DD/YYYY or YYYY-MM-DD.")


def analysis_date_variants(target_date: date) -> list[str]:
    return [
        target_date.strftime("%m/%d/%Y"),
        f"{target_date.month}/{target_date.day}/{target_date.year}",
        target_date.strftime("%Y-%m-%d"),
    ]


def delete_calls_and_analysis(target_date: date, skip_prompt: bool = False) -> None:
    db = SessionLocal()
    try:
        call_id_rows = db.query(Call.call_id).filter(Call.call_date == target_date).all()
        call_ids = [str(row[0]) for row in call_id_rows]
        analysis_date_values = analysis_date_variants(target_date)

        calls_count = len(call_ids)

        analysis_id_rows = (
            db.query(CallAnalysisResult.id)
            .filter(
                or_(
                    CallAnalysisResult.call_id.in_(call_ids) if call_ids else False,
                    CallAnalysisResult.call_recording_date.in_(analysis_date_values),
                )
            )
            .distinct()
            .all()
        )
        analysis_ids = [row[0] for row in analysis_id_rows]
        analysis_count = len(analysis_ids)

        print(f"Target date: {target_date.isoformat()} ({target_date.strftime('%m/%d/%Y')})")
        print(f"Matching calls: {calls_count}")
        print(f"Matching analysis rows: {analysis_count}")

        if calls_count == 0 and analysis_count == 0:
            print("Nothing to delete.")
            return

        if not skip_prompt:
            confirm = input("Delete these rows? Type 'yes' to proceed: ")
            if confirm.strip().lower() != "yes":
                print("Cancelled.")
                return

        deleted_analysis = 0
        if analysis_ids:
            deleted_analysis = (
                db.query(CallAnalysisResult)
                .filter(CallAnalysisResult.id.in_(analysis_ids))
                .delete(synchronize_session=False)
            )

        deleted_calls = (
            db.query(Call)
            .filter(Call.call_date == target_date)
            .delete(synchronize_session=False)
        )

        db.commit()
        print(f"Deleted {deleted_calls} calls and {deleted_analysis} analysis rows.")

        remaining_calls = db.query(Call).filter(Call.call_date == target_date).count()
        remaining_analysis = (
            db.query(CallAnalysisResult)
            .filter(
                or_(
                    CallAnalysisResult.call_id.in_(call_ids) if call_ids else False,
                    CallAnalysisResult.call_recording_date.in_(analysis_date_values),
                )
            )
            .count()
        )
        print(f"Remaining rows for that date - calls: {remaining_calls}, analysis: {remaining_analysis}")

    except Exception as exc:
        db.rollback()
        print(f"Error: {exc}")
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete calls and analysis rows for one date.")
    parser.add_argument(
        "--date",
        default=DEFAULT_DATE,
        help="Target date in MM/DD/YYYY or YYYY-MM-DD format. Default: 7/7/2026",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    args = parser.parse_args()

    target_date = parse_date(args.date)
    delete_calls_and_analysis(target_date, skip_prompt=args.yes)


if __name__ == "__main__":
    main()