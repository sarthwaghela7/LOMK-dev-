from datetime import datetime, timezone
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import uuid
from datetime import timedelta
from sqlalchemy import text
from sqlalchemy import or_

from storage.db import SessionLocal
from storage.models import Call, CallAnalysisResult
from services.Processing_pipeline.pipeline import Pipeline
from services.Database_sync.manual_fields import apply_model_fields
from utils.prompt_config import get_analysis_fields
from utils.config import F2_STT_WORKERS, STT_MODEL
from monitoring.recorder import stt_usage_scope

_WORKER_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
_CLAIM_STALE_AFTER = timedelta(minutes=10)


class F2Processor:
    """
    F2 Processing Stage - STT only.

    F2 is deliberately not responsible for LLM analysis. It only validates that a
    call has an audio path, performs STT, saves the transcript to the `calls`
    table, and commits before F3 starts. This keeps DB transactions short and
    ensures the LLM stage can retry independently without re-running STT.
    """

    def __init__(self):
        # Do NOT open a database session here. We'll open it only when needed
        # for database operations. This allows STT processing to proceed without
        # holding a connection from the pool.
        self.db = None
        self.pipe = Pipeline()
        self._refresh_analysis_fields()

    def _get_db(self):
        """Get or create a database session for this operation."""
        if self.db is None:
            self.db = SessionLocal()
        return self.db

    def claim_calls(self, candidate_ids):
        """Atomically claim STT rows across threads and app processes."""
        if not candidate_ids:
            return []
        db = SessionLocal()
        try:
            stale_cutoff = datetime.now(timezone.utc) - _CLAIM_STALE_AFTER
            rows = db.execute(text("""
                SELECT call_id FROM calls
                WHERE call_id = ANY(:ids)
                  AND (stt_claimed_at IS NULL OR stt_claimed_at < :stale_cutoff)
                FOR UPDATE SKIP LOCKED
            """), {"ids": list(candidate_ids), "stale_cutoff": stale_cutoff}).fetchall()
            won_ids = [row[0] for row in rows]
            if won_ids:
                db.execute(text("""
                    UPDATE calls SET stt_claimed_at=:now, stt_claimed_by=:worker
                    WHERE call_id = ANY(:ids)
                """), {"now": datetime.now(timezone.utc), "worker": _WORKER_ID, "ids": won_ids})
                db.commit()
            return won_ids
        finally:
            db.close()

    def _refresh_analysis_fields(self):
        self.analysis_fields = get_analysis_fields()
        self.analysis_column_names = set(CallAnalysisResult.__table__.columns.keys())

    # dst numbers that should always be categorized as Inbound calls,
    # regardless of what the LLM returns for call_type
    INBOUND_DST_NUMBERS = {"2243605155", "13128781255"}

    def _normalize_dst(self, value):
        """
        Digits-only normalization of a dst value, matching against both the
        10-digit and 11-digit (leading '1') forms so formatting differences
        in the calls table don't cause a miss.
        """
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        return digits

    def _is_inbound_dst(self, dst_value):
        normalized = self._normalize_dst(dst_value)
        if not normalized:
            return False
        return any(
            normalized == self._normalize_dst(target)
            for target in self.INBOUND_DST_NUMBERS
        )

    # Numbers where call_type should be derived directly from src/dst instead
    # of left to the LLM: if this number is the dst, the call is Inbound;
    # if it's the src, the call is Outbound.
    DIRECTIONAL_NUMBERS = {"2243374447"}

    def _is_directional_number(self, value):
        normalized = self._normalize_dst(value)
        if not normalized:
            return False
        return any(
            normalized == self._normalize_dst(target)
            for target in self.DIRECTIONAL_NUMBERS
        )

    @staticmethod
    def _normalize_number(value):
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        return digits

    def fetch_unprocessed_records(
        self,
        start_date=None,
        phone_number=None,
        priority_date=None,
        fallback_limit=None,
        strict_priority_date=False,
    ):
        """
        Fetch unprocessed records from calls table, ordered by date/time.
        - start_date (str 'YYYY-MM-DD' or date): only call_date >= start_date.
        - phone_number (str): only calls where src or dst matches this number,
          normalized to handle with/without leading country code '1'.
        - priority_date: if this date has work, return only that day's calls.
        - fallback_limit: cap historical work when no priority calls exist.
        """
        # Use a temporary session for this query, then close it.
        # This prevents holding a connection while processing unrelated data.
        db = SessionLocal()
        try:
            query = db.query(Call).filter(
                or_(Call.transcript.is_(None), Call.transcript == ""),
                Call.audio_s3_uri.isnot(None),
                Call.audio_s3_uri != "",
                Call.processing_status.in_(["not processed", "not_processed", "failed", "processed"]),
            )

            if start_date is not None:
                if isinstance(start_date, str):
                    start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
                query = query.filter(Call.call_date >= start_date)

            def materialize(scoped_query, limit=None):
                ordered = scoped_query.order_by(Call.call_date.asc(), Call.call_time.asc())
                records = ordered.all()
                # Preserve the existing run-loop behavior: rows already marked
                # processed are never sent back to STT. Filter before applying
                # a fallback cap so completed historical rows cannot hide the
                # next genuinely pending item.
                records = [
                    record for record in records
                    if record.processing_status != "processed"
                ]
                if phone_number is None:
                    return records[:limit] if limit is not None else records
                target = self._normalize_number(phone_number)
                records = [
                    record for record in records
                    if self._normalize_number(record.src) == target
                    or self._normalize_number(record.dst) == target
                ]
                return records[:limit] if limit is not None else records

            if priority_date is not None:
                if isinstance(priority_date, str):
                    priority_date = datetime.strptime(priority_date, "%Y-%m-%d").date()
                priority_records = materialize(
                    query.filter(Call.call_date == priority_date)
                )
                if priority_records:
                    return priority_records
                if strict_priority_date:
                    return []

            return materialize(query, limit=fallback_limit)
        finally:
            db.close()

    def _parse_llm_result(self, llm_result_raw):
        """Legacy compatibility no-op kept only to avoid touching unrelated callers.

        F2 no longer calls the LLM, so the parsing path is intentionally unused.
        The method remains in place to preserve the existing file shape and reduce
        the risk of breaking external imports while the architecture is being split.
        """
        return {"raw_llm_response": llm_result_raw}

    def _check_audio_exists(self, s3_uri):
        """
        Check if audio file exists and has valid size.
        Returns (bool, size_bytes, error_message)
        """
        if not s3_uri or s3_uri.strip() == "":
            return False, 0, "No audio path provided"

        from storage.s3 import S3Service
        s3 = S3Service()

        try:
            bucket, key = s3._parse_s3_uri(s3_uri)
            head = s3.client.head_object(Bucket=bucket, Key=key)
            size = head['ContentLength']

            # A size threshold is not an audio-integrity check. In particular,
            # short valid WAV recordings can be well below 1 KiB. Require only
            # a complete RIFF/WAVE header here; the STT provider remains
            # responsible for rejecting an otherwise unreadable file.
            if size < 44:
                return False, size, f"Audio file too small for a WAV header ({size} bytes)"

            header = s3.client.get_object(
                Bucket=bucket,
                Key=key,
                Range="bytes=0-11",
            )["Body"].read()
            if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                return False, size, "Audio object is not a valid WAV container"

            return True, size, None

        except Exception as e:
            error_msg = str(e)
            if "404" in error_msg or "Not Found" in error_msg or "NoSuchKey" in error_msg:
                return False, 0, "Audio file not found in S3 (404)"
            return False, 0, f"S3 error: {error_msg}"

    @staticmethod
    def _is_permanent_audio_failure(error):
        """Identify audio errors that cannot succeed on a later retry."""
        message = str(error or "").lower()
        return any(
            marker in message
            for marker in (
                "corrupted/empty",
                "too small for a wav header",
                "not a valid wav container",
            )
        )

    def process_record(self, record):
        """
        F2 now performs only STT and transcript persistence.

        Once the transcript is committed, F3 is responsible for the LLM stage and
        `CallAnalysisResult` persistence. This keeps the DB transaction isolated
        from the OpenAI call and avoids holding a long-lived transaction while
        waiting on the LLM API.
        """
        record_id = record.call_id
        start_time = datetime.now(timezone.utc)

        print(f"\n{'='*80}")
        print(f"[F2] ID={record_id} | Date={record.call_date} {record.call_time}")
        print(f"     AUDIO={record.audio_s3_uri}")
        print(f"{'='*80}")

        has_audio, size, audio_error = self._check_audio_exists(record.audio_s3_uri)

        if not has_audio:
            print(f"[{record_id}] ⚠️ F2 skipped: {audio_error}")
            self._mark_failed(
                record_id,
                f"No audio: {audio_error}",
                retryable=not self._is_permanent_audio_failure(audio_error),
            )
            return False

        print(f"[{record_id}] ✓ Audio valid: {size} bytes")

        try:
            print(f"[{record_id}] F2: running STT only...")
            # CRITICAL: STT processing happens WITHOUT a database connection held.
            # This prevents blocking the connection pool during the external API call.
            with stt_usage_scope(record_id, model=STT_MODEL):  # usage ledger only; never raises
                stt_result = self.pipe.stt.transcribe(record.audio_s3_uri)
            print(f"[{record_id}] ✓ STT completed: {len(stt_result)} chars")

            # Now that STT is complete, open a fresh connection to update the database.
            db = SessionLocal()
            try:
                # Re-fetch the record with the fresh session to ensure we have
                # the latest state (in case it was modified by another process).
                current_record = db.query(Call).filter(Call.call_id == record_id).first()
                if not current_record:
                    print(f"[{record_id}] ✗ Record was deleted while processing")
                    return False

                current_record.transcript = stt_result
                current_record.processing_status = "processed"
                current_record.stt_claimed_at = None
                current_record.stt_claimed_by = None
                current_record.error_message = None
                current_record.updated_at = datetime.now(timezone.utc)
                db.add(current_record)
                db.commit()
                print(f"[{record_id}] ✓ Transcript committed to calls table")

                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                print(f"\n[F2 SUCCESS] ID={record_id} | Time={duration:.1f}s")
                print(f"          Transcript={len(stt_result)} chars")
                print(f"{'='*80}")
                return True
            finally:
                db.close()

        except Exception as e:
            print(f"[{record_id}] ✗ F2 failed: {type(e).__name__}: {e}")
            self._mark_failed(
                record_id,
                f"STT error: {type(e).__name__}: {str(e)[:500]}",
                retryable=not self._is_permanent_audio_failure(e),
            )
            return False

    def _mark_failed(self, record_id, error_message, *, retryable=True):
        """
        Mark a call as failed in the calls table, and also insert a
        placeholder row (NULL LLM fields) into call_analysis_results so
        that every call has a matching analysis row, whether it succeeded
        or failed.
        """
        # Use a fresh session for this operation
        db = SessionLocal()
        failed_record = None
        try:
            call = db.query(Call).filter(Call.call_id == record_id).first()
            if call:
                # Network/provider errors can be retried. A corrupt or empty
                # audio object cannot become valid without a new S3 upload, so
                # mark it terminally and keep it out of the scheduled queue.
                call.processing_status = (
                    "not processed" if retryable else "failed_permanent"
                )
                call.error_message = error_message[:500]
                call.stt_claimed_at = None
                call.stt_claimed_by = None
                call.updated_at = datetime.now(timezone.utc)
                db.add(call)
                db.commit()
                print(f"[{record_id}] ✗ Marked as failed: {error_message[:100]}")
                # Copy the values required by the second write before this
                # session is released.  Holding this connection while
                # _create_null_analysis_record() checks out another one made
                # each concurrently failing STT job consume *two* pool slots.
                failed_record = {
                    "call_id": call.call_id,
                    "call_number": call.call_number,
                    "call_reference": call.call_reference,
                    "call_date": call.call_date,
                    "billsec": call.billsec,
                }
        except Exception as e:
            print(f"[{record_id}] DB error marking failed: {e}")
            try:
                db.rollback()
            except:
                pass
        finally:
            db.close()

        # Keep call_analysis_results 1:1 with calls even on failure, but only
        # after returning the status-update connection to the shared pool.
        if failed_record is not None:
            self._create_null_analysis_record(failed_record, error_message)

    def _create_null_analysis_record(self, record, error_message=None):
        """
        Upsert a placeholder row into call_analysis_results for a call that
        failed processing. Identifying/core fields (sourced from the calls
        table, not the LLM) are populated; every LLM-derived field is left
        NULL. If a row already exists for this call_id, it is UPDATED in
        place (never left stale, never duplicated) so every call maps to
        AT MOST ONE call_analysis_results row at all times.
        """
        def value(name):
            return record.get(name) if isinstance(record, dict) else getattr(record, name)

        record_id = value("call_id")
        # Use a fresh session for this operation
        db = SessionLocal()
        try:
            existing = (
                db.query(CallAnalysisResult)
                .filter(CallAnalysisResult.call_id == str(record_id))
                .first()
            )

            self._refresh_analysis_fields()

            analysis_data = {
                "call_id": str(record_id),
                "call_number": value("call_number"),
                "call_reference": value("call_reference"),
                "call_recording_date": str(value("call_date")) if value("call_date") else None,
                "call_recording_date_normalized": value("call_date"),
                "call_duration": str(value("billsec")) if value("billsec") else None,
                "created_at": datetime.now(timezone.utc),
            }

            # Null out every other LLM-derived field so the row has the full schema
            for field in self.analysis_fields:
                key = field["key"]
                if not key or key in analysis_data or key in ("call_id", "created_at"):
                    continue
                if key not in self.analysis_column_names:
                    continue
                analysis_data[key] = None

            analysis_data["raw_llm_json"] = None
            analysis_data["processing_status"] = "failed"
            if "error_message" in self.analysis_column_names:
                analysis_data["error_message"] = (error_message or "")[:500]

            if existing:
                # A failed run must not wipe values an agent already entered.
                apply_model_fields(existing, analysis_data)
                db.add(existing)
                db.commit()
                print(f"[{record_id}] ✓ Existing analysis row updated in place (processing_status=failed)")
                return

            analysis_record = CallAnalysisResult(**analysis_data)
            db.add(analysis_record)
            db.commit()
            print(f"[{record_id}] ✓ Null analysis row inserted (processing_status=failed)")

        except Exception as e:
            print(f"[{record_id}] ✗ Failed to insert null analysis row: {type(e).__name__}: {e}")
            try:
                db.rollback()
            except:
                pass
        finally:
            db.close()

    def run(
        self,
        start_date=None,
        phone_number=None,
        priority_date=None,
        fallback_limit=None,
        max_workers=None,
        strict_priority_date=False,
    ):
        """
        Process unprocessed calls in one pass, but only through STT.

        The DB is the handoff boundary: F2 commits transcript state, and F3 picks
        up the transcript later to run FlintDesk Stage 1 analysis.
        """
        try:
            records = self.fetch_unprocessed_records(
                start_date=start_date,
                phone_number=phone_number,
                priority_date=priority_date,
                fallback_limit=fallback_limit,
                strict_priority_date=strict_priority_date,
            )

            print(f"\n{'#'*80}")
            print(f"# F2 PROCESSOR - STT ONLY")
            print(f"# Processing audio-ready calls with {max_workers or F2_STT_WORKERS} STT worker(s)")
            print(f"{'#'*80}\n")

            if not records:
                print("No unprocessed records found.")
                return

            total = len(records)
            won_ids = set(self.claim_calls([record.call_id for record in records]))
            records = [record for record in records if record.call_id in won_ids]
            total = len(records)
            if not records:
                print("All audio-ready calls are already claimed by another worker.")
                return
            worker_count = max(1, min(max_workers or F2_STT_WORKERS, 4, total))

            print(f"Found {total} unprocessed calls\n")

            def process_one(idx, record):
                # Check if record was already processed (don't hold a connection
                # across the entire batch; check each one fresh)
                db = SessionLocal()
                try:
                    current = db.query(Call).filter(Call.call_id == record.call_id).first()
                    if current and current.processing_status == "processed":
                        print(f"\n[{idx}/{total}] ID={record.call_id} - Already processed, skipping")
                        return None
                finally:
                    db.close()

                print(f"\n[{idx}/{total}] " + "-"*60)

                try:
                    return self.process_record(record)
                except Exception as e:
                    print(f"[{record.call_id}] UNEXPECTED ERROR: {e}")
                    self._mark_failed(record.call_id, f"Unexpected error: {type(e).__name__}: {str(e)[:500]}")
                    return False

            results = []
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="f2-stt") as executor:
                futures = [executor.submit(process_one, idx, record) for idx, record in enumerate(records, 1)]
                for future in as_completed(futures):
                    results.append(future.result())
            processed = sum(result is True for result in results)
            failed = sum(result is False for result in results)

            print(f"\n{'#'*80}")
            print(f"# F2 COMPLETE")
            print(f"# Total: {total} | Processed: {processed} | Failed: {failed}")
            print(f"{'#'*80}\n")
        finally:
            self.close()

    def close(self):
        try:
            if self.db is not None:
                self.db.close()
        except:
            pass


if __name__ == "__main__":
    import sys
    start_date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    phone_number_arg = sys.argv[2] if len(sys.argv) > 2 else None

    processor = F2Processor()
    try:
        processor.run(start_date=start_date_arg, phone_number=phone_number_arg)
    finally:
        processor.close()
