"""Read-only database/S3 audit. Never queues or modifies production records."""
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sqlalchemy import create_engine, text
from utils.config import DATABASE_URL


def main():
    output = Path(__file__).resolve().parents[3] / "verification"
    output.mkdir(exist_ok=True)
    summary = {"checked_at": datetime.now(timezone.utc).isoformat(), "destination": "3128781255", "read_only": True}
    engine = create_engine(DATABASE_URL, connect_args={"connect_timeout": 10, "options": "-c statement_timeout=30000"})
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            rows = conn.execute(text("""
                SELECT c.call_id, c.call_reference, c.call_date, c.call_time, c.src, c.dst, c.billsec, c.disposition,
                       c.audio_s3_bucket, c.audio_s3_key, c.audio_s3_uri,
                       c.processing_status, c.fd_processing_status, c.error_message,
                       length(trim(coalesce(c.transcript, ''))) AS transcript_chars,
                       a.id AS analysis_id, a.processing_status AS autoflint_status,
                       length(trim(coalesce(a.fd_a_call_summary, ''))) AS summary_chars
                FROM calls c LEFT JOIN call_analysis_results a ON a.call_id = c.call_id
                WHERE regexp_replace(coalesce(c.dst, ''), '[^0-9]', '', 'g') IN ('3128781255', '13128781255')
                ORDER BY c.call_date, c.call_time, c.call_id
            """)).mappings().all()
            logs = conn.execute(text("""
                SELECT source_system, sync_action, batch_date, status, count(*) AS entries,
                       sum(failed_records) AS failed_records, max(completed_at) AS last_completed
                FROM sync_logs
                WHERE batch_date IN (SELECT DISTINCT call_date FROM calls
                    WHERE regexp_replace(coalesce(dst, ''), '[^0-9]', '', 'g') IN ('3128781255', '13128781255'))
                GROUP BY source_system, sync_action, batch_date, status
                ORDER BY batch_date
            """)).mappings().all()
            (output / "inbound-sync-log-evidence.json").write_text(json.dumps([dict(row) for row in logs], default=str, indent=2), encoding="utf-8")
    except Exception as exc:
        # Avoid printing a SQLAlchemy exception containing credentials or SQL parameters.
        summary["blocker"] = type(exc).__name__
        summary["diagnostic"] = str(getattr(exc, "orig", exc)).splitlines()[0]
        (output / "inbound-audit-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
        return
    finally:
        engine.dispose()

    from storage.s3 import S3Service
    from services.Database_sync.f1 import F1Processor
    s3 = S3Service()
    probe = F1Processor()
    # List each relevant date once to find recordings that exist but were never linked.
    date_objects = {}
    for call_date in sorted({row["call_date"] for row in rows}):
        prefix = f"vitel-call-recordings/{call_date:%Y/%m/%d}/"
        try:
            objects = []
            for page in s3.client.get_paginator("list_objects_v2").paginate(Bucket=probe.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    if obj["Key"].lower().endswith(".wav"):
                        parsed = probe._parse_audio_filename(obj["Key"])
                        if parsed and probe._normalize_phone(parsed["dst"]) == "3128781255":
                            objects.append(parsed)
            date_objects[call_date] = objects
        except Exception as exc:
            date_objects[call_date] = None
            summary.setdefault("listing_errors", {})[str(call_date)] = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
    results = []
    cache = {}
    for raw in rows:
        row = dict(raw)
        ref = row.pop("audio_s3_uri") or row.pop("audio_s3_key", None)
        bucket = row.pop("audio_s3_bucket", None)
        row.pop("audio_s3_key", None)
        row["linked_recording"] = ref or ""
        audio = {"s3_status": "no linked object", "audio_seconds": None}
        if ref:
            parsed_bucket, key = s3._parse_s3_uri(ref)
            bucket = bucket or parsed_bucket
            identity = (bucket, key)
            if identity not in cache:
                try:
                    head = s3.client.head_object(Bucket=bucket, Key=key)
                    probe.bucket = bucket
                    duration = probe._get_wav_duration_seconds(key)
                    cache[identity] = {"s3_status": "available", "s3_bytes": head["ContentLength"],
                                       "audio_seconds": duration, "audio_validation": "WAV metadata valid" if duration else "duration unverified"}
                except Exception as exc:
                    code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
                    cache[identity] = {"s3_status": code, "audio_seconds": None}
            audio = cache[identity]
        row.update(audio)
        candidates = date_objects[row["call_date"]]
        row["unlinked_candidates"] = "listing unavailable" if candidates is None else " | ".join(
            obj["s3_key"] for obj in candidates
            if probe._normalize_phone(obj["src"]) == probe._normalize_phone(row["src"])
            and abs((obj["call_datetime"] - datetime.combine(row["call_date"], row["call_time"])).total_seconds()) <= probe.MATCH_TOLERANCE_SECONDS
        )
        row["f1_evidence"] = "DB record and recording linked" if ref else "DB record exists; no recording linked"
        row["f2_evidence"] = "transcript persisted" if row["transcript_chars"] else "no transcript; completion unproven"
        row["f3_evidence"] = "summary persisted" if row["summary_chars"] else "no FlintDesk summary; completion unproven"
        row["review_required"] = not (audio.get("audio_seconds") and row["transcript_chars"] and row["summary_chars"] and row["fd_processing_status"] == "processed") or bool(row["error_message"])
        row["duration_mismatch_seconds"] = abs(audio["audio_seconds"] - (row["billsec"] or 0)) if audio.get("audio_seconds") else None
        if row["duration_mismatch_seconds"] is not None and row["duration_mismatch_seconds"] > 30:
            row["review_required"] = True
        row["automated_f3_eligible_date"] = str(row["call_date"]) >= "2026-08-17"
        row["recommended_action"] = (
            "No reprocessing needed" if not row["review_required"] else
            "Verify recording identity: audio and billsec differ by more than 30 seconds" if row["duration_mismatch_seconds"] and row["duration_mismatch_seconds"] > 30 else
            "Inspect/repair invalid recording before F2" if ref and not audio.get("audio_seconds") else
            "Review recording candidate and rerun scoped F1 matching, then F2/F3" if not ref and row["unlinked_candidates"] else
            "Investigate missing recording; cannot run F2/F3 without audio" if not ref else
            "Review errors and use scoped F2/F3 retry"
        )
        results.append(row)
    fields = sorted({key for row in results for key in row})
    with (output / "inbound-audit.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    summary.update({"rows": len(results), "unique_calls": len({str(row["call_id"]) for row in results}),
                    "s3_statuses": dict(Counter(row["s3_status"] for row in results)),
                    "review_required": sum(bool(row["review_required"]) for row in results),
                    "recommended_actions": dict(Counter(row["recommended_action"] for row in results)),
                    "limitations": "Persisted artifacts prove outputs exist, not historical execution timestamps. WAV headers do not prove full audio decodability. Database scope cannot prove Vitel calls missing from the database were ingested."})
    (output / "inbound-audit-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
