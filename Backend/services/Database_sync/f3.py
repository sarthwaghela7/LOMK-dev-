import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone

from sqlalchemy import case, func, or_, text

from services.Processing_pipeline.fd_llm_node import (
    FDLLM,
    FD_AI_FIELDS as FD_FIELD_KEYS,
    get_and_reset_rate_limit_hits,
)
from services.Database_sync.manual_fields import apply_model_fields
from services.voicemail_content import apply_content_voicemail_flag
from services.Processing_pipeline.llm_node import LLM as AutoFlintLLM
from storage.db import SessionLocal
from storage.models import Call, CallAnalysisResult
from services.vitel_metadata import apply_metadata_voicemail
from utils.config import F3_LLM_WORKERS, STAGE2_AI_ENABLED
from services.fd_prompt_store import get_stage2_prompts
from services.calls_page_scope import is_on_calls_page
from services.stage2_automation import apply_stage2_result, extract_stage2
from services.analysis_jobs import AnalysisJobStore, AnalysisBusy, AnalysisNeedsReview, db_error_summary, execute_analysis

# Self-correcting worker cap: shrinks toward 1 across runs when 429s persist
# even after the shared rate limiter has throttled down, and recovers back
# toward F3_LLM_WORKERS after a clean run. No manual retuning required.
_adaptive_worker_cap = {"value": F3_LLM_WORKERS}


class F3Processor:
    """
    F3 is the shared LLM orchestration layer for both AutoFlint and FlintDesk.

    Application-specific LLM logic remains isolated:

        AutoFlint
            -> llm_node.py

        FlintDesk
            -> fd_llm_node.py

    F3 is responsible for:
        1. Finding transcript-ready calls.
        2. Processing them in FIFO order.
        3. Calling the correct LLM node.
        4. Persisting the result.

    IMPORTANT:
    AutoFlint and FlintDesk use independent processing states.

        AutoFlint
            -> CallAnalysisResult.processing_status

        FlintDesk
            -> Call.fd_processing_status

    This prevents completion of one application's analysis from
    preventing the other application's analysis.
    """

    # ------------------------------------------------------------------
    # F3 PROCESSING CUTOFF
    # ------------------------------------------------------------------
    # Only calls from this date onwards are eligible for the automated
    # F3 pipeline.
    #
    # This means:
    #     2026-08-17 and later -> PROCESS
    #     Before 2026-08-17    -> IGNORE
    #
    # Old July data remains safely in the database. It is simply not
    # selected by the automated F3 processing query.
    #
    # The date is based on the system's Central-time processing window.
    # ------------------------------------------------------------------
    F3_START_DATE = date(2026, 8, 17)

    def __init__(self, application="autoflint"):
        self.application = (
            application or "autoflint"
        ).strip().lower()

        # Do NOT open a database session here. We'll open it only when needed
        # for actual database operations. This allows LLM processing to proceed
        # without holding a connection from the pool.
        self.db = None
        self.llm = self._build_llm()

    # ------------------------------------------------------------------
    # LLM selection
    # ------------------------------------------------------------------
    def _build_llm(self):
        """
        Select the application-specific LLM node.

        FlintDesk:
            fd_llm_node.py

        AutoFlint:
            llm_node.py
        """

        if self.application == "flintdesk":
            return FDLLM()

        return AutoFlintLLM()

    # ------------------------------------------------------------------
    # Field handling
    # ------------------------------------------------------------------
    def _get_field_keys(self):
        """
        Return the fields belonging to the current application.
        """

        if self.application == "flintdesk":
            return list(FD_FIELD_KEYS)

        return list(self._auto_flint_field_keys())

    def _auto_flint_field_keys(self):
        """
        Load AutoFlint analysis fields from the existing prompt
        configuration.
        """

        from utils.prompt_config import get_analysis_fields

        fields = get_analysis_fields()

        return [
            field["key"]
            for field in fields
            if field.get("key")
        ]

    def _normalize_payload(self, payload, field_keys):
        """
        Normalize the LLM response so every expected field is present.
        """

        cleaned = {}

        for key in field_keys:
            value = (
                payload.get(key, None)
                if isinstance(payload, dict)
                else None
            )

            if value is None:
                cleaned[key] = None

            elif isinstance(value, str):
                cleaned[key] = value.strip() or None

            elif isinstance(value, (dict, list)):
                cleaned[key] = value

            else:
                cleaned[key] = str(value)

        return cleaned

    # ------------------------------------------------------------------
    # CHANGED: Date normalization
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_call_date(call_date):
        """
        Convert a call date into one canonical format:

            YYYY-MM-DD

        call_recording_date is currently a TEXT column, so F3 must
        explicitly control the format instead of relying on:

            str(call_date)

        This prevents inconsistent values such as:

            17/08/2026
            2026-08-17

        from being written by different processing paths.
        """

        if call_date is None:
            return None

        # --------------------------------------------------------------
        # If SQLAlchemy already returned a Python date/datetime object,
        # format it directly.
        # --------------------------------------------------------------
        if hasattr(call_date, "strftime"):
            return call_date.strftime("%Y-%m-%d")

        value = str(call_date).strip()

        if not value:
            return None

        # --------------------------------------------------------------
        # Handle ISO format:
        #
        #     2026-08-17
        #     2026-08-17T14:30:00
        # --------------------------------------------------------------
        try:
            return datetime.fromisoformat(
                value.replace("Z", "+00:00")
            ).strftime("%Y-%m-%d")
        except ValueError:
            pass

        # --------------------------------------------------------------
        # Handle existing DD/MM/YYYY format:
        #
        #     17/08/2026
        # --------------------------------------------------------------
        try:
            return datetime.strptime(
                value,
                "%d/%m/%Y",
            ).strftime("%Y-%m-%d")
        except ValueError:
            pass

        # --------------------------------------------------------------
        # Do not silently create another inconsistent date format.
        # Raise an error so the problem is visible in the logs.
        # --------------------------------------------------------------
        raise ValueError(
            f"Unsupported call date format: {value}"
        )

    # ------------------------------------------------------------------
    # Fetch transcript-ready calls
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_phone_number(value):
        digits = "".join(char for char in str(value or "") if char.isdigit())
        return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits

    @staticmethod
    def _normalized_phone_expression(column):
        """SQL equivalent of ``_normalize_phone_number`` for scoped runs."""
        digits = func.regexp_replace(func.coalesce(column, ""), r"\D", "", "g")
        return case(
            (func.length(digits) == 11, func.substr(digits, 2)),
            else_=digits,
        )

    def fetch_transcript_ready_calls(
        self,
        start_date=None,
        phone_number=None,
        priority_date=None,
        fallback_limit=None,
        strict_priority_date=False,
    ):
        """
        Fetch calls whose transcript has already been committed by F2.

        ONLY calls from F3_START_DATE onwards are considered.

        Calls are processed chronologically:

            call_date ASC
            call_time ASC

        This provides FIFO behavior without introducing an
        additional in-memory queue.

        FlintDesk:
            Only fd_processing_status is checked.

        AutoFlint:
            Only CallAnalysisResult.processing_status is checked.

        Therefore one application's completion status can never
        block the other application.
        """
        
        # Use a temporary session for this query, then close it.
        # This prevents holding a connection while the LLM processes calls.
        db = SessionLocal()
        try:
            query = (
                db.query(Call)
                .filter(
                    # ------------------------------------------------------
                    # F2 must have already produced a transcript.
                    # ------------------------------------------------------
                    Call.transcript.isnot(None),
                    Call.transcript != "",
                )
                .order_by(
                    # ------------------------------------------------------
                    # FIFO:
                    # oldest eligible call first.
                    # ------------------------------------------------------
                    Call.call_date.asc(),
                    Call.call_time.asc(),
                )
            )

            # AutoFlint retains its historical rollout boundary. FlintDesk must
            # process every transcript-ready call, including the historical
            # backlog; the old shared cutoff silently stranded earlier calls.
            if self.application != "flintdesk":
                query = query.filter(Call.call_date >= self.F3_START_DATE)

            if start_date is not None:
                if isinstance(start_date, str):
                    start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
                query = query.filter(Call.call_date >= start_date)

            # Apply a caller-supplied number scope in SQL before materializing
            # calls or inspecting analysis rows.  Previously this happened after
            # an N+1 completed-analysis lookup over the entire transcript backlog,
            # making a small backfill appear to hang before llm_node.py ran.
            if phone_number is not None:
                requested_number = self._normalize_phone_number(phone_number)
                query = query.filter(
                    or_(
                        self._normalized_phone_expression(Call.src) == requested_number,
                        self._normalized_phone_expression(Call.dst) == requested_number,
                    )
                )

            def eligible_calls(scoped_query, limit=None):
                candidate_calls = scoped_query.all()
                jobs = db.execute(text("""SELECT call_id, status FROM analysis_jobs
                    WHERE application=:app AND status <> 'completed'"""),
                    {"app": self.application}).all()
                ready_ids = {str(call_id) for call_id, status in jobs if status == "response_ready"}
                blocked_ids = {str(call_id) for call_id, status in jobs if status != "response_ready"}
                candidate_calls = [call for call in candidate_calls if str(call.call_id) not in blocked_ids]

                if self.application == "flintdesk":
                    calls = [
                        call for call in candidate_calls
                        if call.fd_processing_status != "processed" or str(call.call_id) in ready_ids
                    ]
                    return calls[:limit] if limit is not None else calls

                # One batched lookup replaces one query per candidate call.
                # Failed AutoFlint analyses remain retryable; completed ones do not.
                call_ids = [str(call.call_id) for call in candidate_calls]
                completed_ids = set()
                if call_ids:
                    completed_ids = {
                        str(call_id)
                        for (call_id,) in (
                            db.query(CallAnalysisResult.call_id)
                            .filter(CallAnalysisResult.call_id.in_(call_ids))
                            .filter(CallAnalysisResult.processing_status == "completed")
                            .all()
                        )
                    }
                calls = [
                    call for call in candidate_calls
                    if str(call.call_id) not in completed_ids or str(call.call_id) in ready_ids
                ]
                return calls[:limit] if limit is not None else calls

            if priority_date is not None:
                if isinstance(priority_date, str):
                    priority_date = datetime.strptime(priority_date, "%Y-%m-%d").date()
                priority_calls = eligible_calls(
                    query.filter(Call.call_date == priority_date)
                )
                if priority_calls:
                    return priority_calls
                if strict_priority_date:
                    return []

            return eligible_calls(query, limit=fallback_limit)
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Compatibility for callers of the former batch-claim API
    # ------------------------------------------------------------------
    def claim_calls(self, candidate_ids):
        """Compatibility for backfill callers; admission happens in process_record.

        Never lease waiting batches. The durable job guards every entry point
        immediately before a request is sent, for both applications.
        """
        return list(candidate_ids)

    # ------------------------------------------------------------------
    # Analysis record
    # ------------------------------------------------------------------
    def _get_or_create_analysis_record(self, call, db):
        """
        Get the existing analysis row for a call or create one.

        Both AutoFlint and FlintDesk use the same analysis row, but
        they write their own application-specific fields.
        
        Database connection must be passed in (already open).
        """

        analysis = (
            db.query(CallAnalysisResult)
            .filter(
                CallAnalysisResult.call_id
                == str(call.call_id)
            )
            .first()
        )

        if analysis:
            # Legacy/placeholder rows created before the normalized-date
            # column was consistently populated would otherwise stay NULL
            # forever, which hides them from /calls/analysis date filtering.
            if analysis.call_recording_date_normalized is None and call.call_date:
                analysis.call_recording_date_normalized = call.call_date
            return analysis

        # --------------------------------------------------------------
        # CHANGED:
        # Normalize the date before writing it to the TEXT field.
        # This guarantees YYYY-MM-DD instead of relying on str(date).
        # --------------------------------------------------------------
        normalized_date = self._normalize_call_date(
            call.call_date
        )

        analysis = CallAnalysisResult(
            call_id=str(call.call_id),
            call_number=call.call_number,
            call_reference=call.call_reference,

            # CHANGED:
            # Always store the canonical YYYY-MM-DD representation.
            call_recording_date=normalized_date,

            # CHANGED:
            # Populate the proper DATE column as well.
            #
            # This assumes call_recording_date_normalized exists in
            # the SQLAlchemy CallAnalysisResult model, as it does in
            # your database schema.
            call_recording_date_normalized=call.call_date,

            call_duration=(
                str(call.billsec)
                if call.billsec
                else None
            ),
            created_at=datetime.now(timezone.utc),
        )

        db.add(analysis)
        # The session has autoflush off, so without this the row is invisible to the next lookup in the same
        # transaction (the Stage 2 save runs right after Stage 1) and a second row for the same call would be
        # created, which the database rejects (call_analysis_results_call_id_key) and rolls the whole save back.
        db.flush()

        return analysis

    # ------------------------------------------------------------------
    # AutoFlint persistence
    # ------------------------------------------------------------------
    def _persist_autoflint_result(self, record, payload, db=None):
        """
        Persist the AutoFlint LLM result.

        AutoFlint owns:
            CallAnalysisResult.processing_status
        """
        
        # Use a fresh connection for this database operation
        call_id = str(record.call_id)
        owns_session = db is None
        db = db if db is not None else SessionLocal()
        try:
            # See _persist_flintdesk_result: re-fetch fresh in this session
            # rather than reusing the caller's still-attached object.
            record = db.query(Call).filter(Call.call_id == call_id).first()
            if not record:
                return

            field_keys = self._get_field_keys()

            validated = self._normalize_payload(
                payload,
                field_keys,
            )

            analysis = self._get_or_create_analysis_record(record, db)

            # Never overwrite what an agent typed (e.g. the agent name on an
            # inbound call) with the model's answer on a later analysis run.
            apply_model_fields(
                analysis,
                {key: validated.get(key) for key in field_keys},
            )
            # TEMPORARY: decide voicemail from what was said, not the outcome text.
            apply_content_voicemail_flag(analysis, getattr(record, "transcript", None))

            analysis.raw_llm_json = validated

            # AutoFlint completion state.
            analysis.processing_status = "completed"

            record.error_message = None
            record.updated_at = datetime.now(timezone.utc)

            db.add(record)
            db.add(analysis)

            if owns_session:
                db.commit()
        finally:
            if owns_session:
                db.close()

    # ------------------------------------------------------------------
    # AutoFlint response parsing
    # ------------------------------------------------------------------
    def _parse_auto_flint_response(self, response):
        """
        Parse the AutoFlint LLM response into a dictionary.
        """

        if isinstance(response, dict):
            return response

        if not isinstance(response, str):
            return {
                "raw_llm_response": str(response)
            }

        cleaned = response.strip()

        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]

        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]

        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

        cleaned = cleaned.strip()

        if not cleaned:
            return {}

        try:
            parsed = json.loads(cleaned)

            if isinstance(parsed, dict):
                return parsed

            return {
                "raw_llm_response": parsed
            }

        except json.JSONDecodeError:

            try:
                start = cleaned.find("{")
                end = cleaned.rfind("}")

                if (
                    start != -1
                    and end != -1
                    and end > start
                ):
                    parsed = json.loads(
                        cleaned[start:end + 1]
                    )

                    if isinstance(parsed, dict):
                        return parsed

                    return {
                        "raw_llm_response": parsed
                    }

            except json.JSONDecodeError:
                pass

            return {
                "raw_llm_response": cleaned
            }

    # ------------------------------------------------------------------
    # FlintDesk persistence
    # ------------------------------------------------------------------
    def _persist_flintdesk_result(self, record, payload, db=None):
        """
        Persist the FlintDesk LLM result.

        IMPORTANT:
        FlintDesk does NOT modify:

            CallAnalysisResult.processing_status

        because that field belongs to the AutoFlint analysis state.

        FlintDesk instead uses:

            Call.fd_processing_status

        This keeps both application pipelines independent.
        """
        
        # Use a fresh connection for this database operation
        call_id = str(record.call_id)
        owns_session = db is None
        db = db if db is not None else SessionLocal()
        try:
            # `record` was loaded (and is still attached) in the caller's own
            # session. Re-fetch it fresh here instead of reusing that object,
            # since a SQLAlchemy object can only belong to one open session
            # at a time -- passing it to db.add() below would otherwise
            # raise "Object is already attached to session X".
            record = db.query(Call).filter(Call.call_id == call_id).first()
            if not record:
                return

            field_keys = self._get_field_keys()

            validated = self._normalize_payload(
                payload,
                field_keys,
            )

            analysis = self._get_or_create_analysis_record(record, db)

            # Write only the FlintDesk fd_a_* fields.
            apply_model_fields(
                analysis,
                {key: validated.get(key) for key in field_keys},
            )
            # TEMPORARY: decide voicemail from what was said, not the outcome text.
            apply_content_voicemail_flag(analysis, getattr(record, "transcript", None))

            # Store the FlintDesk response without marking the shared
            # AutoFlint processing_status as completed.
            analysis.raw_llm_json = validated

            # FlintDesk has its own independent completion state.
            record.fd_processing_status = "processed"
            record.fd_claimed_at = None
            record.fd_claimed_by = None

            record.error_message = None
            record.updated_at = datetime.now(timezone.utc)

            db.add(record)
            db.add(analysis)

            if owns_session:
                db.commit()
        finally:
            if owns_session:
                db.close()

    def _persist_failure_state(self, record, exc):
        """
        Persist failure state when LLM processing fails.
        
        Creates analysis row if needed and updates call status.
        Uses fresh database connection.
        """
        call_id = str(record.call_id)
        
        db = SessionLocal()
        try:
            # Re-fetch the record with fresh session to get latest state
            record = db.query(Call).filter(Call.call_id == call_id).first()
            if not record:
                print(f"[F3:{self.application}] {call_id} - Record was deleted")
                return
            
            # Application-specific failure state
            if self.application == "flintdesk":
                record.fd_processing_status = "not processed"
                record.fd_claimed_at = None
                record.fd_claimed_by = None
                record.error_message = f"F3 failed: {type(exc).__name__}: {str(exc)[:500]}"
            else:
                record.processing_status = "not processed"
                record.error_message = f"F3 failed: {type(exc).__name__}: {str(exc)[:500]}"

            record.updated_at = datetime.now(timezone.utc)

            db.add(record)

            # Ensure an analysis row exists
            existing_analysis = (
                db.query(CallAnalysisResult)
                .filter(CallAnalysisResult.call_id == call_id)
                .first()
            )

            if existing_analysis is None:
                # Use canonical date normalization
                normalized_date = self._normalize_call_date(record.call_date)

                existing_analysis = CallAnalysisResult(
                    call_id=call_id,
                    call_number=record.call_number,
                    call_reference=record.call_reference,
                    call_recording_date=normalized_date,
                    call_recording_date_normalized=record.call_date,
                    call_duration=str(record.billsec) if record.billsec else None,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(existing_analysis)

            # Only AutoFlint owns processing_status.
            # FlintDesk failure must NOT mark the shared AutoFlint processing status.
            if self.application != "flintdesk":
                existing_analysis.processing_status = "failed"

            existing_analysis.raw_llm_json = {"error": str(exc)[:1000]}

            db.commit()
        except Exception as save_exc:
            db.rollback()
            print(f"[F3:{self.application}] {call_id} - Failed to persist failure state: {save_exc}")
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Stage 2 AI automation (FlintDesk only; behind FD_STAGE2_AI_ENABLED)
    # ------------------------------------------------------------------
    def _stage2_prompts(self, record=None):
        """Active fd_prompts when Stage 2 should ride along on the Stage 1 request, else None.

        Stage 2 is only for calls listed in the Call Records table; Missed Calls
        entries (routing legs etc.) are skipped.
        """
        if self.application != "flintdesk" or not STAGE2_AI_ENABLED:
            return None
        if record is not None and not is_on_calls_page(record):
            return None
        prompts = get_stage2_prompts()
        if prompts is None:
            print("[F3:flintdesk] Stage 2 skipped; fd_prompts not configured")
        return prompts

    @staticmethod
    def _with_stage2(record, response, prompts):
        """Attach the validated Stage 2 result to the response that gets checkpointed.

        Stage 2 comes from the same single request as Stage 1 (the "stage2" key),
        so there is no extra paid call. It is stored under "_stage2" beside the
        Stage 1 response, so a saved job replays without a new request. A missing
        or unusable Stage 2 never fails Stage 1; use regenerate-stage2 to retry it.
        """
        if not prompts:
            return response
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return response
        result = extract_stage2(content, record)
        if result is None:
            print(f"[F3:flintdesk] {record.call_id}: Stage 2 result missing or invalid; Stage 1 saved")
            return response
        result["prompt_versions"] = {f"{key}_version": prompt["version"] for key, prompt in prompts.items()}
        return {**response, "_stage2": result}

    def _apply_stage2(self, record, response, db, replace_manual=False):
        result = response.get("_stage2") if isinstance(response, dict) else None
        if not result:
            return []
        analysis = self._get_or_create_analysis_record(record, db)
        return apply_stage2_result(analysis, result, result.get("prompt_versions") or {}, replace_manual=replace_manual)

    def _apply_stage2_isolated(self, record, response, db):
        """Save Stage 2 in its own savepoint so a Stage 2 failure can never undo the Stage 1 AI analysis.

        Stage 1 values are flushed when the savepoint opens, so a Stage 1 problem still fails loudly (and is
        retried); only a Stage 2 problem is contained, reported without row data, and left for regenerate-stage2.
        """
        try:
            with db.begin_nested():
                self._apply_stage2(record, response, db)
        except Exception as exc:  # noqa: BLE001 - Stage 2 must never break Stage 1
            print(f"[F3:{self.application}] {record.call_id}: Stage 2 not saved ({db_error_summary(exc)}); Stage 1 saved")

    # ------------------------------------------------------------------
    # Process one record
    # ------------------------------------------------------------------
    def process_record(self, record, request_key=None, raise_errors=False):
        """Admit once, durably checkpoint, then atomically save and complete."""
        if not (record.transcript or "").strip():
            return False
        store = AnalysisJobStore(SessionLocal)

        def apply(db, response):
            parsed = self.llm.parse_response(response, record)
            if self.application == "flintdesk":
                # Vitel now explicitly reports VOICEMAIL in disposition.
                # AI-based voicemail classification is bypassed for
                # metadata-confirmed voicemail records: the LLM still runs
                # (summary, name, etc. from the recording), but its voicemail
                # verdict is overridden by the metadata.
                parsed = apply_metadata_voicemail(parsed, record)
                self._persist_flintdesk_result(record, parsed, db=db)
                self._apply_stage2_isolated(record, response, db)
            else:
                payload = self._parse_auto_flint_response(parsed)
                self._persist_autoflint_result(record, payload, db=db)

        def generate():
            prompts = self._stage2_prompts(record)
            if prompts:
                response = self.llm.request_analysis(record.transcript, record, stage2_prompts=prompts)
            else:
                response = self.llm.request_analysis(record.transcript, record)
            return self._with_stage2(record, response, prompts)

        try:
            changed = execute_analysis(
                store, record.call_id, self.application,
                generate,
                apply, request_key=request_key,
            )
            return True if changed else None
        except (AnalysisBusy, AnalysisNeedsReview):
            if raise_errors:
                raise
            print(f"[F3:{self.application}] {record.call_id}: analysis blocked; no new request sent")
            return None
        except Exception as exc:
            # Do not reset shared call flags or overwrite a saved AI response.
            # The job retains either the response for replay or an uncertain
            # request state that requires review before any new paid request.
            if raise_errors:
                raise
            print(f"[F3:{self.application}] {record.call_id}: {db_error_summary(exc)}; see analysis_jobs")
            return False

    # ------------------------------------------------------------------
    # Run F3
    # ------------------------------------------------------------------
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
        Process all transcript-ready calls sequentially.

        FIFO order:
            call_date ASC
            call_time ASC

        Only calls from F3_START_DATE onwards are considered.

        No separate Python queue is required.

        The database itself acts as the pending-work source.
        
        CRITICAL: Database connections are NOT held between calls.
        Each call's LLM processing happens without a database connection.
        """

        calls = (
            self.fetch_transcript_ready_calls(
                start_date=start_date,
                phone_number=phone_number,
                priority_date=priority_date,
                fallback_limit=fallback_limit,
                strict_priority_date=strict_priority_date,
            )
        )

        print(
            f"[F3:{self.application}] "
            f"{len(calls)} transcript-ready "
            f"call(s) queued for processing "
            f"from {self.F3_START_DATE} onwards"
        )

        # Backlog depth metric: helps confirm whether concurrency is keeping up.
        print(f"[F3:{self.application}] pending queue depth = {len(calls)}")

        worker_count = max(1, min(max_workers or _adaptive_worker_cap["value"], F3_LLM_WORKERS, len(calls) or 1))

        def process_one(idx, call):

            print(
                f"[F3:{self.application}] "
                f"[{idx}/{len(calls)}] "
                f"{call.call_id} | "
                f"{call.call_date} "
                f"{call.call_time}"
            )

            # Fetch fresh copy of call to ensure we have latest state
            # (call from fetch_transcript_ready_calls may be detached)
            db = SessionLocal()
            try:
                current_call = db.query(Call).filter(Call.call_id == call.call_id).first()
                if current_call:
                    db.expunge(current_call)
                else:
                    return None
            finally:
                db.close()

            return self.process_record(current_call)

        results = []
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="f3-llm") as executor:
            futures = [executor.submit(process_one, idx, call) for idx, call in enumerate(calls, 1)]
            for future in as_completed(futures):
                results.append(future.result())
        processed = sum(result is True for result in results)
        failed = sum(result is False for result in results)

        rate_limit_hits = get_and_reset_rate_limit_hits()

        # Self-verify: if 429s are still happening despite the shared
        # token-bucket throttle, the worker pool itself is oversubscribed
        # for the account's tier. Shrink it (floor of 1) instead of relying
        # on the user to notice and reconfigure it manually.
        if self.application == "flintdesk" and rate_limit_hits > 0:
            previous_cap = _adaptive_worker_cap["value"]
            _adaptive_worker_cap["value"] = max(1, previous_cap - 1)
            if _adaptive_worker_cap["value"] < previous_cap:
                print(
                    f"[F3:{self.application}] WARNING: {rate_limit_hits} OpenAI 429(s) this run "
                    f"despite adaptive throttling; reducing F3 worker cap {previous_cap} -> "
                    f"{_adaptive_worker_cap['value']}. If this keeps happening at cap=1, the "
                    f"OpenAI account tier is insufficient for current call volume."
                )
        elif self.application == "flintdesk" and _adaptive_worker_cap["value"] < F3_LLM_WORKERS:
            # Clean run: ease back toward the configured worker count.
            _adaptive_worker_cap["value"] = min(F3_LLM_WORKERS, _adaptive_worker_cap["value"] + 1)

        print(
            f"[F3:{self.application}] "
            f"completed: "
            f"processed={processed}, "
            f"failed={failed}, "
            f"openai_429_hits={rate_limit_hits}"
        )

        return {
            "processed": processed,
            "failed": failed,
            "rate_limit_hits": rate_limit_hits,
            "worker_cap": _adaptive_worker_cap["value"] if self.application == "flintdesk" else worker_count,
        }


# ------------------------------------------------------------------
# Manual execution
# ------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run AutoFlint transcript analysis")
    parser.add_argument(
        "--start-date",
        help="Only analyze calls on or after this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--phone-number",
        help="Restrict analysis to one source or destination number",
    )
    args = parser.parse_args()
    F3Processor(application="autoflint").run(
        start_date=args.start_date,
        phone_number=args.phone_number,
    )
