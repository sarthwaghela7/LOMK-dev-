"""Categorise every EXISTING call record into a lifecycle stage (migration 073 must be applied first).

Each call record is classified from its own Case Status Change value; client number, context, lead and agent are never
looked at, and nothing is de-duplicated. For calls that are In-Transition now, the timer start (in_transition_entered_at) comes from,
in order of trust:
  1. fd_case_status_history: the latest change INTO an In-Transition status from a non-In-Transition one;
  2. fd_m_date_entry: the date the status was last changed (never earlier than the true entry, so never falsely old);
  3. otherwise the migration moment (timer starts now, documented policy). call_date is NEVER used.
Existing Lost outcomes (legacy "PC shared and lost") keep stage Lost; lost_at comes from the same two sources.

Dry run by default: prints the counts per stage, the timestamp source breakdown and ambiguous values, and writes NOTHING.
  python -m ops.backfill_case_lifecycle                       # report only
  python -m ops.backfill_case_lifecycle --apply [--batch 2000] # writes only where case_lifecycle_stage IS NULL (rerun-safe)

Never run against production without the owner's approval; the data stays otherwise untouched (only the five lifecycle
columns are written, in short batches).
"""
import argparse

from sqlalchemy import text

from storage.db import SessionLocal
from services.case_lifecycle import UNMAPPED_LEGACY_STATUSES

ENTRY_FROM_HISTORY = """
    (SELECT max(h.changed_at) FROM fd_case_status_history h
      WHERE h.analysis_id = a.id AND fd_case_lifecycle_stage_of(h.new_status) IN ('in_transition', 'lost')
        AND fd_case_lifecycle_stage_of(h.previous_status) NOT IN ('in_transition'))
"""
DATE_ENTRY = "(a.fd_m_date_entry::timestamp AT TIME ZONE 'America/Chicago')"


def report(db):
    stage_counts = db.execute(text("""
        SELECT fd_case_lifecycle_stage_of(a.fd_m_case_status_change) AS stage, count(*) FROM call_analysis_results a
        WHERE a.case_lifecycle_stage IS NULL GROUP BY 1 ORDER BY 1""")).all()
    print("Uncategorised call records by stage:")
    for stage, count in stage_counts:
        print(f"  {stage:<14}{count}")
    print(f"  {'TOTAL':<14}{sum(count for _, count in stage_counts)}")
    already = db.execute(text("SELECT count(*) FROM call_analysis_results WHERE case_lifecycle_stage IS NOT NULL")).scalar()
    print(f"Already categorised: {already}")
    sources = db.execute(text(f"""
        SELECT CASE WHEN {ENTRY_FROM_HISTORY} IS NOT NULL THEN '1 status history'
                    WHEN a.fd_m_date_entry IS NOT NULL THEN '2 fd_m_date_entry'
                    ELSE '3 migration moment (no reliable timing)' END AS source, count(*)
        FROM call_analysis_results a
        WHERE a.case_lifecycle_stage IS NULL AND fd_case_lifecycle_stage_of(a.fd_m_case_status_change) = 'in_transition'
        GROUP BY 1 ORDER BY 1""")).all()
    print("In-Transition timer start source:")
    for source, count in sources:
        print(f"  {source:<42}{count}")
    print("Values needing an owner decision / unrecognised (classified Open meanwhile):")
    odd = db.execute(text("""
        SELECT coalesce(a.fd_m_case_status_change, '(NULL)'), count(*) FROM call_analysis_results a
        WHERE a.case_lifecycle_stage IS NULL AND btrim(coalesce(a.fd_m_case_status_change, '')) <> ''
          AND fd_case_lifecycle_stage_of(a.fd_m_case_status_change) = 'open'
          AND lower(btrim(a.fd_m_case_status_change)) NOT IN ('open', 'blocker', 'same client context')
        GROUP BY 1 ORDER BY 2 DESC""")).all()
    for value, count in odd or [("(none)", 0)]:
        flag = "  <- no counterpart in the new dropdown" if str(value).strip().lower() in UNMAPPED_LEGACY_STATUSES else ""
        print(f"  {value!r}: {count}{flag}")


BACKFILL = f"""
    UPDATE call_analysis_results a SET
        case_lifecycle_stage = fd_case_lifecycle_stage_of(a.fd_m_case_status_change),
        in_transition_entered_at = CASE WHEN fd_case_lifecycle_stage_of(a.fd_m_case_status_change) = 'in_transition'
            THEN coalesce({ENTRY_FROM_HISTORY}, {DATE_ENTRY}, now()) END,
        last_in_transition_status = CASE WHEN fd_case_lifecycle_stage_of(a.fd_m_case_status_change) = 'in_transition'
            THEN a.fd_m_case_status_change END,
        lost_at = CASE WHEN fd_case_lifecycle_stage_of(a.fd_m_case_status_change) = 'lost'
            THEN coalesce({ENTRY_FROM_HISTORY}, {DATE_ENTRY}, now()) END,
        case_lifecycle_updated_at = now()
    WHERE a.id IN (SELECT id FROM call_analysis_results WHERE case_lifecycle_stage IS NULL ORDER BY id LIMIT :batch FOR UPDATE SKIP LOCKED)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch", type=int, default=2000)
    args = parser.parse_args()
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY")) if not args.apply else None
        report(db)
        if not args.apply:
            print("\nDry run: nothing written. Re-run with --apply after the owner approves.")
            return
    total = 0
    while True:
        with SessionLocal() as db, db.begin():
            done = db.execute(text(BACKFILL), {"batch": args.batch}).rowcount
        total += done
        print(f"categorised {total}")
        if done < args.batch:
            break
    with SessionLocal() as db:
        left = db.execute(text("SELECT count(*) FROM call_analysis_results WHERE case_lifecycle_stage IS NULL")).scalar()
    print(f"Done. Records still uncategorised: {left}")


if __name__ == "__main__":
    main()
