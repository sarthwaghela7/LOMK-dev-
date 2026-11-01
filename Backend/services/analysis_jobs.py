"""Never repeat a paid request merely because saving its result failed.

All entry points acquire a job immediately before model I/O. A generating job
is never automatically reclaimed: after a crash/timeout the provider may already
have charged. Saved responses, however, can safely be applied again. Application
updates and job completion commit in the same transaction.
"""
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


def db_error_summary(exc):
    """Where a database error happened, WITHOUT any row values: "IntegrityError [23505] table=x constraint=y column=z".

    The driver's message text can contain the data being written, so only the structured location fields are used.
    Non-database errors give just their class name.
    """
    name = type(exc).__name__
    orig = getattr(exc, "orig", None) or exc
    diag = getattr(orig, "diag", None)
    code = getattr(orig, "pgcode", None)
    if diag is None and code is None:
        return name
    parts = [f"[{code}]" if code else ""]
    for label, attr in (("table", "table_name"), ("constraint", "constraint_name"), ("column", "column_name")):
        value = getattr(diag, attr, None) if diag is not None else None
        if value:
            parts.append(f"{label}={value}")
    return " ".join([name] + [part for part in parts if part])


class AnalysisBusy(Exception):
    pass


class AnalysisNeedsReview(Exception):
    pass


class AnalysisJobStore:
    def __init__(self, session_factory):
        self.sessions = session_factory

    def acquire(self, call_id, application, request_key=None):
        if application not in {"autoflint", "flintdesk"}:
            raise ValueError("Unknown analysis application")
        params = {"call_id": str(call_id), "app": application}
        problem = None
        with self.sessions() as db:
            with db.begin():
                # Serialize admission for this call across processes and endpoints.
                call = db.execute(text("SELECT fd_processing_status FROM calls WHERE call_id=:call_id FOR UPDATE"), params).mappings().first()
                if call is None:
                    raise ValueError("Call no longer exists")
                if request_key:
                    params["key"] = str(uuid.UUID(str(request_key)))
                    previous = db.execute(text("""SELECT id, status FROM analysis_jobs
                        WHERE call_id=:call_id AND application=:app AND request_key=:key"""), params).mappings().first()
                    if previous and previous["status"] == "completed":
                        return None
                job = db.execute(text("""SELECT * FROM analysis_jobs
                    WHERE call_id=:call_id AND application=:app AND status <> 'completed'
                    FOR UPDATE"""), params).mappings().first()
                if job:
                    if request_key and str(job["request_key"]) != params["key"]:
                        raise AnalysisBusy("An earlier analysis must finish before re-analysis can start.")
                    if job["status"] == "response_ready":
                        return (str(job["id"]), False)
                    if job["status"] == "review_required":
                        problem = AnalysisNeedsReview("Previous AI request needs review; no new request was sent.")
                    elif job["updated_at"] < datetime.now(timezone.utc) - timedelta(minutes=15):
                        db.execute(text("""UPDATE analysis_jobs SET status='review_required',
                            error='Worker stopped or request outcome unknown', updated_at=now() WHERE id=:id"""), {"id": job["id"]})
                        problem = AnalysisNeedsReview("Previous AI request outcome is unknown; no new request was sent.")
                    else:
                        problem = AnalysisBusy("Analysis is already running. Please wait.")
                else:
                    if not request_key:
                        completed = call["fd_processing_status"] == "processed" if application == "flintdesk" else db.execute(text("""
                            SELECT EXISTS(SELECT 1 FROM call_analysis_results
                            WHERE call_id=:call_id AND processing_status='completed')
                        """), params).scalar()
                        if completed:
                            return None
                    job_id = str(uuid.uuid4())
                    db.execute(text("""INSERT INTO analysis_jobs
                        (id, call_id, application, request_key, status)
                        VALUES (:id, :call_id, :app, :key, 'generating')"""),
                        {**params, "id": job_id, "key": params.get("key")})
                    return (job_id, True)
        # Raise outside the transaction so review_required is committed.
        raise problem

    def checkpoint(self, job_id, response):
        # Retry only this DB write, never the model request. If all writes fail,
        # the generating job remains blocked and cannot trigger another charge.
        encoded = json.dumps(response)
        for attempt in range(3):
            try:
                with self.sessions() as db:
                    with db.begin():
                        db.execute(text("""UPDATE analysis_jobs SET response=CAST(:response AS JSONB),
                            status='response_ready', error=NULL, updated_at=now()
                            WHERE id=:id AND status IN ('generating', 'review_required')"""),
                            {"id": job_id, "response": encoded})
                return
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(0.2 * (attempt + 1))

    def note_failure(self, job_id, exc):
        with self.sessions() as db:
            with db.begin():
                db.execute(text("""UPDATE analysis_jobs SET
                    status=CASE WHEN response IS NULL THEN 'review_required' ELSE status END,
                    error=:error, updated_at=now() WHERE id=:id AND status <> 'completed'"""),
                    {"id": job_id, "error": db_error_summary(exc) + ': analysis interrupted; inspect server logs'})

    def persist(self, job_id, apply):
        with self.sessions() as db:
            with db.begin():
                # Use the same lock order as acquire to avoid deadlocks.
                call_id = db.execute(text("SELECT call_id FROM analysis_jobs WHERE id=:id"), {"id": job_id}).scalar_one()
                db.execute(text("SELECT call_id FROM calls WHERE call_id=:id FOR UPDATE"), {"id": call_id})
                job = db.execute(text("SELECT * FROM analysis_jobs WHERE id=:id FOR UPDATE"), {"id": job_id}).mappings().one()
                if job["status"] == "completed":
                    return
                if job["status"] != "response_ready":
                    raise AnalysisNeedsReview("AI response is not available for recovery.")
                apply(db, job["response"])
                db.execute(text("""UPDATE analysis_jobs SET status='completed', error=NULL,
                    updated_at=now() WHERE id=:id"""), {"id": job_id})


def execute_analysis(store, call_id, application, generate, apply, request_key=None):
    job = store.acquire(call_id, application, request_key)
    if job is None:
        return False  # Existing result; no paid request.
    job_id, needs_generation = job
    try:
        if needs_generation:
            store.checkpoint(job_id, generate())
        store.persist(job_id, apply)
        return True
    except Exception as exc:
        try:
            store.note_failure(job_id, exc)
        except Exception:
            pass  # Existing durable state still prevents another paid request.
        raise
