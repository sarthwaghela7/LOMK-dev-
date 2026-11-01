"""Classify RFE / I-130 / I-485 / H4 / H4EAD on STORED transcripts for a date range (the October 2026 backfill).

Dry run by default: reports what would happen and makes NO model request and NO write. Add --apply to run it.

  python -m ops.backfill_priority_cases --from 2026-10-01 --to 2026-10-20             # preview
  python -m ops.backfill_priority_cases --from 2026-10-01 --to 2026-10-20 --apply     # run

Every call with a stored transcript is sent to the model; there is no keyword filter. Calls an earlier version stored as
"nothing found" without a model request (analysis_source 'prefilter', result rev 1) are picked up again automatically.

Safe to stop (Ctrl-C) and re-run: every call already analysed for its current transcript and prompt version is skipped, so a
re-run only does the remaining work. Calls without a transcript are only counted, never transcribed. Work is done in
bounded batches, one request at a time, throttled to --rpm requests per minute (default 20) on top of F3's shared back-off
for 429s, so it leaves room for the live F1/F2/F3 workers (a separate process has its own rate limiter).
Needs migration 074 applied. Does not need the live worker (PRIORITY_CASES_ENABLED) to be on.
"""
import argparse
import sys
from datetime import date, datetime

import pytz

from services import priority_cases as pc
from storage.db import SessionLocal

CT = pytz.timezone("US/Central")


def _day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _estimate_tokens(chars: int) -> int:
    return chars // 4   # rough: ~4 characters per token. An estimate only; real usage is measured and printed after --apply.


def preview(start: date, end: date, version: int, limit: int) -> None:
    with SessionLocal() as db:
        rows = pc.candidates(db, start=start, end=end, version=version, limit=limit, reanalyse=True)
        state = pc.coverage(db, start=start, end=end, version=version)
    to_model = rows   # every eligible transcript is sent: there is no keyword gate
    hinted = sum(1 for r in rows if pc.candidate_forms(r["transcript"], r.get("hints")))
    chars = sum(min(len(r["transcript"] or ""), pc.MAX_TRANSCRIPT_CHARS) for r in to_model)
    print(f"Range {start} .. {end}, prompt version {version}")
    print(f"  calls in range without a transcript (never transcribed here): {state['missing_transcript']}")
    print(f"  calls with a transcript: {state['with_transcript']}; already up to date: {state['up_to_date']}")
    print(f"  to process now (this preview is capped at --limit {limit}): {len(rows)}")
    print(f"    of which name a form by keyword (diagnostic only, not a gate): {hinted}; no keyword at all: {len(rows) - hinted}")
    print(f"    would be sent to the model (one request each, upper bound; Missed-Calls legs are dropped when applying): {len(to_model)}")
    print(f"    rough input estimate: ~{_estimate_tokens(chars):,} transcript tokens plus ~{len(to_model) * 650:,} prompt tokens (estimate, not measured)")
    print("Dry run: no model requests, no writes. Re-run with --apply.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="start", type=_day, default=date(2026, 10, 1), help="first call date (default 2026-10-01)")
    parser.add_argument("--to", dest="end", type=_day, default=None, help="last call date, inclusive (default: today, US/Central)")
    parser.add_argument("--limit", type=int, default=5000, help="most calls to process in this run (default 5000)")
    parser.add_argument("--batch-size", type=int, default=25, help="calls per batch (default 25)")
    parser.add_argument("--rpm", type=int, default=20, help="most model requests per minute from this process (default 20)")
    parser.add_argument("--apply", action="store_true", help="actually call the model and write results")
    args = parser.parse_args()
    end = args.end or datetime.now(CT).date()
    if end < args.start:
        print("--to must be on or after --from", file=sys.stderr)
        return 2

    prompt = pc.active_prompt()
    if prompt is None:
        print("No active 'priority_case_classification' prompt in fd_prompts; apply migration 074 first.", file=sys.stderr)
        return 1
    if not args.apply:
        preview(args.start, end, prompt["version"], args.limit)
        return 0

    from services.Processing_pipeline import fd_llm_node
    from services.Processing_pipeline.fd_llm_node import FDLLM
    from services.calls_page_scope import calls_page_call_ids
    from utils.rate_limiter import TokenBucketRateLimiter

    fd_llm_node._RATE_LIMITER = TokenBucketRateLimiter(max(1, args.rpm))

    def listed(day):
        with SessionLocal() as db:
            return calls_page_call_ids(db, day)

    totals = {"eligible": 0, "analysed": 0, "not_listed": 0, "keyword_hint": 0, "no_keyword_hint": 0, "matched": 0, "uncertain": 0, "failed": 0,
              "llm_requests": 0, "input_tokens": 0, "output_tokens": 0}
    remaining = args.limit
    try:
        while remaining > 0:
            counts = pc.run_batch(SessionLocal, FDLLM, start=args.start, end=end, limit=min(args.batch_size, remaining),
                                  prompt=prompt, is_listed=listed, reanalyse=True)
            if counts.get("error"):
                print(counts["error"], file=sys.stderr)
                return 1
            for key in totals:
                totals[key] += counts[key]
            remaining -= counts["eligible"]
            print(f"batch: {counts}")
            if counts["provider_busy"]:
                print("Provider asked us to slow down or credits are exhausted; stopped. Re-run later to continue.")
                break
            # Stop when nothing new was handled (everything left is failing or claimed elsewhere), to avoid spinning.
            handled = counts["analysed"] + counts["not_listed"] + counts["failed"]
            if counts["eligible"] == 0 or handled == 0:
                break
    except KeyboardInterrupt:
        print("Interrupted; already-saved results are kept. Re-run to continue.")

    with SessionLocal() as db:
        state = pc.coverage(db, start=args.start, end=end, version=prompt["version"])
    print("Run totals:", totals)
    print("State of the range:", state)
    print("Measured token usage this run (as reported by the provider):",
          f"input={totals['input_tokens']:,} output={totals['output_tokens']:,} over {totals['llm_requests']} request(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
