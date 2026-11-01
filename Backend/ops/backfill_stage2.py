"""Fill blank Stage 2 fields (Self Action Item, Action Due Date, Follow Up Dates) with AI for one call date.

Only calls on --date that are listed on the Calls page (Call Records table) and have a transcript and at least one blank Stage 2 field are sent to the
model. Values an agent already typed are never overwritten (same guard as the live pipeline); the
due-date limit (< 72h after the call's entry time) and follow-up rules are enforced by the backend.
Dry run by default: lists the calls that would be processed and makes NO model request and NO write.

  python -m ops.backfill_stage2 --date 2026-10-09 [--limit 50] [--apply]

Needs the fd_prompts migrations applied. Does not need FD_STAGE2_AI_ENABLED (that flag is for the live pipeline).
"""
import argparse
from datetime import datetime

from sqlalchemy import text

from services.Processing_pipeline.fd_llm_node import FDLLM
from services.calls_page_scope import calls_page_call_ids
from services.fd_prompt_store import get_stage2_prompts
from services.stage2_automation import apply_stage2_result
from storage.db import SessionLocal
from storage.models import Call, CallAnalysisResult


def _blank_sql(column):
    return f"({column} IS NULL OR btrim({column}) IN ('', '-'))"


CANDIDATES = f"""
    SELECT c.call_id
    FROM calls c
    LEFT JOIN call_analysis_results a ON a.call_id::text = c.call_id::text
    WHERE c.call_date = :day
      AND c.transcript IS NOT NULL AND btrim(c.transcript) <> ''
      AND (a.fd_stage2_ai_meta->'fd_m_self_action_item') IS NULL  -- AI has not already handled this call
      AND (a.call_id IS NULL
           OR {_blank_sql('a.fd_m_self_action_item')}
           OR {_blank_sql('a.fd_m_follow_up_dates')}
           OR a.fd_m_action_due_date IS NULL)
    ORDER BY c.call_time ASC
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="call date, YYYY-MM-DD (business/Central date)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    day = datetime.strptime(args.date, "%Y-%m-%d").date()

    with SessionLocal() as db:
        ids = [row[0] for row in db.execute(text(CANDIDATES), {"day": day}).all()]
        # Only calls listed in the Call Records table; Missed Calls entries are left alone.
        listed = calls_page_call_ids(db, day)
    hidden = [call_id for call_id in ids if str(call_id) not in listed]
    ids = [call_id for call_id in ids if str(call_id) in listed]
    if hidden:
        print(f"Skipping {len(hidden)} call(s) not listed on the Calls page (Missed Calls / routing records)")
    if args.limit:
        ids = ids[: args.limit]
    print(f"{len(ids)} call(s) on {day} with a transcript and blank Stage 2 field(s)")
    if not args.apply:
        for call_id in ids:
            print("  would process", call_id)
        print("Dry run: no model requests, no writes. Re-run with --apply.")
        return

    prompts = get_stage2_prompts()
    if prompts is None:
        raise SystemExit("fd_prompts has no complete active prompt set; run migration 069 first.")
    llm = FDLLM()
    filled = skipped = failed = 0
    for call_id in ids:
        try:
            with SessionLocal() as db:
                call = db.query(Call).filter(Call.call_id == call_id).first()
                db.expunge(call)  # no open transaction during the model request
            result = llm.generate_stage2(call.transcript, call, prompts)
            if result is None:
                skipped += 1
                print(call_id, "no usable result; unchanged")
                continue
            with SessionLocal() as db:
                db.execute(text("SELECT call_id FROM calls WHERE call_id = :i FOR UPDATE"), {"i": call_id})
                analysis = db.query(CallAnalysisResult).filter(CallAnalysisResult.call_id == str(call_id)).first()
                if analysis is None:
                    skipped += 1
                    print(call_id, "no analysis row (Stage 1 not run yet); unchanged")
                    continue
                written = apply_stage2_result(analysis, result, result.get("prompt_versions") or {})
                db.commit()
            filled += 1
            print(call_id, "filled:", ", ".join(written) or "nothing (all fields already had values)")
        except Exception as exc:  # keep going; one bad call must not stop the batch
            failed += 1
            print(call_id, f"failed: {type(exc).__name__}")
    print(f"Done: {filled} updated, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
