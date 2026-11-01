"""Re-classify fd_a_client_type for Missed Calls onto the dropdown values.

Only touches fd_a_client_type, and only for calls linked to a missed_call task
whose current value is not one of the allowed values.  Dry run by default.

  python -m ops.backfill_client_type --since 2026-09-29 [--apply]
"""
import argparse
import json
from datetime import datetime

from sqlalchemy import text

from services.Processing_pipeline.fd_llm_node import CLIENT_TYPE_VALUES, FDLLM, normalize_client_type
from storage.db import SessionLocal

PROMPT = """Classify the caller in this call transcript. Return only JSON: {{"fd_a_client_type": "..."}}.
The value must be EXACTLY one of: {values}, or null if the transcript does not support any.
New = first-time/prospective client enquiring; Existing = already a client or has an ongoing case with the firm.
Individual = a person calling about their own matter; Organization = a company, employer or law office calling for
an organisation. Voicemail Call = only a voicemail greeting or message. Spam / Wrong Call = sales/robocall or wrong number.
Use only the transcript. Do not guess.

Transcript:
{transcript}
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    since = datetime.strptime(args.since, "%Y-%m-%d").date()
    llm = FDLLM()
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT DISTINCT c.call_id, c.transcript, a.fd_a_client_type
            FROM tasks t
            JOIN calls c ON c.call_id = t.fd_m_tasks_call_id
            JOIN call_analysis_results a ON a.call_id::text = c.call_id::text
            WHERE t.task_type = 'missed_call' AND c.call_date >= :since
              AND c.transcript IS NOT NULL AND c.transcript <> ''
        """), {"since": since}).all()
        todo = [r for r in rows if normalize_client_type(r.fd_a_client_type) is None]
        print(f"{len(rows)} missed-call analyses since {since}; {len(todo)} need a client type")
        for row in todo:
            reply = llm._post_with_retry({
                "model": llm.model,
                "messages": [{"role": "user", "content": PROMPT.format(
                    values=", ".join(f'"{v}"' for v in CLIENT_TYPE_VALUES), transcript=row.transcript)}],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            }).json()
            value = normalize_client_type(json.loads(reply["choices"][0]["message"]["content"]).get("fd_a_client_type"))
            print(row.call_id, repr(row.fd_a_client_type), "->", value)
            if args.apply:
                db.execute(text("UPDATE call_analysis_results SET fd_a_client_type = :v WHERE call_id::text = :i"),
                           {"v": value, "i": str(row.call_id)})
                db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
