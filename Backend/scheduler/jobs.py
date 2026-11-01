import logging
import os
import time
from datetime import datetime, timedelta
from functools import wraps
from threading import Lock

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.exc import OperationalError

from services.Database_sync.f1 import F1Processor
from services.Database_sync.f2 import F2Processor
from services.Database_sync.f3 import F3Processor
from services.Database_sync.synchronisation import Synchronisation
from utils.config import PIPELINE_FALLBACK_BATCH_SIZE


logger = logging.getLogger("autoflint.scheduler")


# ------------------------------------------------------------------
# Timezone
# ------------------------------------------------------------------
# All automated pipeline date/time decisions use US/Central.
CT = pytz.timezone("US/Central")


# ------------------------------------------------------------------
# Scheduler
# ------------------------------------------------------------------
scheduler = BackgroundScheduler(timezone=CT)
F1_LOCK = Lock()
F2_LOCK = Lock()
F3_LOCK = Lock()


def skip_if_stages_busy(*locks):
    """Prevent the normal, fallback, and daily paths overlapping a stage."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            acquired = []
            for lock in locks:
                if not lock.acquire(blocking=False):
                    for held in reversed(acquired):
                        held.release()
                    print(f"[scheduler] Skipping {func.__name__}: pipeline stage is already active")
                    return None
                acquired.append(lock)
            try:
                return func(*args, **kwargs)
            finally:
                for held in reversed(acquired):
                    held.release()
        return wrapper
    return decorator


# ------------------------------------------------------------------
# Database retry helper
# ------------------------------------------------------------------
def retry_db_error(max_attempts=3, delay_seconds=2):
    """
    Retry transient database/Pooler errors before failing the
    scheduled job.
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)

                except OperationalError as exc:
                    if attempt == max_attempts:
                        raise

                    print(
                        f"[scheduler] DB operation failed "
                        f"({func.__name__}), retrying "
                        f"{attempt}/{max_attempts - 1} "
                        f"in {delay_seconds}s: {exc}"
                    )

                    time.sleep(delay_seconds)

        return wrapper

    return decorator


# ==================================================================
# DAILY SAFETY-NET SYNCHRONIZATION
# ==================================================================
@skip_if_stages_busy(F1_LOCK, F2_LOCK, F3_LOCK)
@retry_db_error(max_attempts=3, delay_seconds=2)
def run_daily_sync():
    """
    Existing daily safety-net synchronization.

    This function is intentionally kept separate from the
    near-real-time F1/F2/F3 workers.
    """

    print("=" * 70)
    print("APScheduler triggered! (daily safety-net sync)")
    print("UTC Time :", datetime.now(pytz.UTC))
    print("CT Time  :", datetime.now(CT))
    print("=" * 70)

    try:
        Synchronisation().run()

        print(
            "[daily-sync] Synchronization completed successfully."
        )

    except Exception as e:
        import traceback

        print(
            f"[daily-sync] Synchronization failed: {e}"
        )

        traceback.print_exc()


# ==================================================================
# F1 - RUN EVERY MINUTE
# ==================================================================
@skip_if_stages_busy(F1_LOCK)
@retry_db_error(max_attempts=3, delay_seconds=2)
def run_f1_every_minute():
    """
    F1 is intentionally an independent scheduler job.

    IMPORTANT:
    F1 does NOT call F2 or F3.

    Every minute:

        F1
        ↓
        Fetch Vitel call metadata
        ↓
        Filter / deduplicate / map audio
        ↓
        Commit calls to database

    Once F1 finishes, it returns immediately.

    F2 and F3 have their own independent workers, so a slow F2/F3
    process can NEVER block the next F1 execution.

    The database acts as the pending-work store.
    """

    try:
        # ----------------------------------------------------------
        # Always use the current US/Central date.
        #
        # Example:
        # If it is August 17 in Central time, F1 processes:
        #
        #     2026-08-17
        #
        # and not the UTC calendar date.
        # ----------------------------------------------------------
        today = datetime.now(CT).date()

        print("=" * 70)
        print(
            f"[F1-worker] Starting F1 for Central date: {today}"
        )
        print(
            f"[F1-worker] UTC time: {datetime.now(pytz.UTC)}"
        )
        print(
            f"[F1-worker] Central time: {datetime.now(CT)}"
        )
        print("=" * 70)

        # ----------------------------------------------------------
        # F1 ONLY.
        #
        # F1 commits its DB changes internally before returning.
        # ----------------------------------------------------------
        F1Processor().run(
            target_date=today
        )

        print(
            f"[F1-worker] F1 completed for {today}"
        )

        # ----------------------------------------------------------
        # Any call F1 just kept with dst = 602 / 2243860601 (Hannah AI)
        # gets its Missed Call task created right here, in the same
        # per-minute tick -- not a separate scheduled job. See
        # services/missed_call_sync.py; idempotent, so re-running every
        # minute is safe (a call that already has a linked task is skipped).
        # ----------------------------------------------------------
        run_missed_call_sync(today)

    except Exception as e:
        import traceback

        print(
            f"[F1-worker] F1 failed: {e}"
        )

        traceback.print_exc()


# ==================================================================
# F2 - STT WORKER
# ==================================================================
@skip_if_stages_busy(F2_LOCK)
@retry_db_error(max_attempts=3, delay_seconds=2)
def run_f2_worker():
    """
    Independent F2 STT worker.

    F2 does NOT depend on the F1 scheduler execution.

    It simply checks the database for calls that are ready for STT.

    Example:

        F1 inserts 31 calls
                    ↓
                Database
                    ↓
              F2 finds 31
                    ↓
        Call 1 → STT → COMMIT
        Call 2 → STT → COMMIT
        Call 3 → STT → COMMIT
        ...
        Call 31 → STT → COMMIT

    If F1 finds more calls while F2 is working, those calls remain
    in the database and will be picked up by the next F2 worker run.

    F2 processes calls sequentially.
    """

    f2 = None

    try:
        print("=" * 70)
        print("[F2-worker] Starting F2 STT worker")
        print("UTC Time :", datetime.now(pytz.UTC))
        print("CT Time  :", datetime.now(CT))
        print("=" * 70)

        # ----------------------------------------------------------
        # F2 only performs STT.
        #
        # F2 does NOT call the LLM.
        # ----------------------------------------------------------
        f2 = F2Processor()

        today = datetime.now(CT).date()
        print(f"[F2-worker] Processing current Central date only: {today}")
        f2.run(start_date=today)

        print(
            "[F2-worker] F2 STT worker completed"
        )

    except Exception as e:
        import traceback

        print(
            f"[F2-worker] F2 failed: {e}"
        )

        traceback.print_exc()

    finally:
        # ----------------------------------------------------------
        # Close the F2 database session.
        # ----------------------------------------------------------
        if f2 is not None:
            try:
                f2.close()
            except Exception as close_error:
                print(
                    f"[F2-worker] Error closing F2: "
                    f"{close_error}"
                )


# ==================================================================
# F3 - FLINTDESK LLM WORKER
# ==================================================================
@retry_db_error(max_attempts=3, delay_seconds=2)
def run_open_call_dependency_ai():
    """Background AI read of who each OPEN call's pending work depends on (services/dependency_ai.py).

    Registered only when OPEN_CALL_DEPENDENCY_AI_ENABLED is on. Each run analyses a small batch of calls that have no
    saved (or an out-of-date) answer, so the Dashboard only ever counts saved results.

    It deliberately does NOT take F3_LOCK and does NOT use F3's LLM client or rate limiter: it steps aside whenever F3 has
    calls in flight (dependency_ai.f3_is_busy) and a provider slow-down only stops this job.
    """
    from utils import config as _config
    if not _config.OPEN_CALL_DEPENDENCY_AI_ENABLED:
        return
    from storage.db import SessionLocal
    from services import dependency_ai

    db = SessionLocal()
    try:
        counts = dependency_ai.run_batch(db, dependency_ai.DependencyLLM())
        if counts["candidates"] or counts["yielded_to_f3"]:
            print(f"[dependency-ai] {counts}")
    except Exception as exc:  # noqa: BLE001 - the next run retries; never take the scheduler down
        print(f"[dependency-ai] run failed: {exc}")
    finally:
        db.close()


def run_flintdesk_f3_worker():
    """
    Independent FlintDesk F3 worker.

    F3 looks for calls that already have a committed transcript.

    It does NOT wait for the F2 scheduler job to finish completely.

    Example:

        F2 processes:

        Call 1 → transcript committed
                     ↓
        F3 can pick Call 1 immediately

        Call 2 → transcript committed
                     ↓
        F3 can pick Call 2

    Therefore F2 and F3 can work independently.

    FlintDesk always uses:

        fd_llm_node.py

    AutoFlint's llm_node.py is NOT called here.
    """

    f3 = None

    try:
        print("=" * 70)
        print("[F3-worker] Starting FlintDesk F3 worker")
        print("UTC Time :", datetime.now(pytz.UTC))
        print("CT Time  :", datetime.now(CT))
        print("=" * 70)

        # ----------------------------------------------------------
        # Explicitly select FlintDesk.
        #
        # This causes F3 to use:
        #
        # services/Processing_pipeline/fd_llm_node.py
        # ----------------------------------------------------------
        f3 = F3Processor(
            application="flintdesk"
        )

        today = datetime.now(CT).date()
        print(f"[F3-worker] Processing current Central date only: {today}")
        result = f3.run(start_date=today)

        print(
            f"[F3-worker] FlintDesk F3 completed: {result}"
        )

    except Exception as e:
        import traceback

        print(
            f"[F3-worker] FlintDesk F3 failed: {e}"
        )

        traceback.print_exc()

    finally:
        # ----------------------------------------------------------
        # F3.run() already closes its DB session in the current F3
        # implementation.
        #
        # This block is intentionally kept defensive in case F3
        # initialization itself creates a session before run().
        # ----------------------------------------------------------
        if f3 is not None:
            try:
                f3.db.close()
            except Exception:
                pass


# ==================================================================
# DAILY AUTOFLINT F3
# ==================================================================
@retry_db_error(max_attempts=3, delay_seconds=2)
def run_daily_autoflint_analysis():
    """
    Daily AutoFlint LLM analysis.

    This is completely separate from the real-time FlintDesk
    pipeline.

    AutoFlint:

        F3
        ↓
        llm_node.py

    It does NOT run every minute.
    It runs once per day.
    """

    f3 = None

    try:
        print("=" * 70)
        print("APScheduler triggered! (daily AutoFlint F3)")
        print("UTC Time :", datetime.now(pytz.UTC))
        print("CT Time  :", datetime.now(CT))
        print("=" * 70)

        # ----------------------------------------------------------
        # Explicitly select AutoFlint.
        #
        # This causes F3 to use:
        #
        # services/Processing_pipeline/llm_node.py
        # ----------------------------------------------------------
        f3 = F3Processor(
            application="autoflint"
        )

        result = f3.run()

        print(
            f"[daily-autoflint] AutoFlint F3 completed: {result}"
        )

    except Exception as e:
        import traceback

        print(
            f"[daily-autoflint] AutoFlint F3 failed: {e}"
        )

        traceback.print_exc()

    finally:
        if f3 is not None:
            try:
                f3.db.close()
            except Exception:
                pass


# ==================================================================
# BACKWARD-COMPATIBILITY WRAPPER
# ==================================================================
def run_near_realtime_process():
    """
    Compatibility wrapper for any old/manual callers.

    IMPORTANT:
    This function is NOT scheduled.

    The automated architecture now uses independent workers:

        F1 -> run_f1_every_minute()
        F2 -> run_f2_worker()
        F3 -> run_flintdesk_f3_worker()

    Keeping this function prevents old imports/manual callers from
    immediately breaking.
    """

    try:
        run_f2_worker()
        run_flintdesk_f3_worker()

    except Exception as e:
        import traceback

        print(
            f"[near-realtime-process] F2/F3 failed: {e}"
        )

        traceback.print_exc()


def run_pipeline_fallback():
    """Bounded recovery for current/yesterday Central-time records.

    Each stage is independently skipped when its normal worker is active. The
    work is idempotent: F1 only attaches missing audio, F2 only transcribes
    missing transcripts, and F3 only analyses unfinished FlintDesk calls.
    """
    today = datetime.now(CT).date()
    dates = (today, today - timedelta(days=1))

    if F1_LOCK.acquire(blocking=False):
        try:
            for target_date in dates:
                result = F1Processor().rematch_missing_audio(target_date, None)
                print(f"[fallback:F1] {target_date}: {result}")
        finally:
            F1_LOCK.release()

    if F2_LOCK.acquire(blocking=False):
        try:
            for target_date in dates:
                F2Processor().run(
                    priority_date=target_date,
                    fallback_limit=PIPELINE_FALLBACK_BATCH_SIZE,
                    strict_priority_date=True,
                )
        finally:
            F2_LOCK.release()

    if F3_LOCK.acquire(blocking=False):
        try:
            for target_date in dates:
                F3Processor(application="flintdesk").run(
                    priority_date=target_date,
                    fallback_limit=PIPELINE_FALLBACK_BATCH_SIZE,
                    strict_priority_date=True,
                )
        finally:
            F3_LOCK.release()


def run_missed_call_sync(target_date):
    """Turn target_date's Hannah AI calls (dst = 602 or 2243860601) into Missed Call tasks.

    Called directly from run_f1_every_minute right after F1 commits, not
    scheduled on its own -- F1 fetching a dst=602/2243860601 call and that
    call getting a Missed Call task are the same event, not two things that
    happen to run around the same time. See
    services/missed_call_sync.sync_missed_calls_for_date for the actual
    matching/creation logic; it's idempotent, so re-running every minute is
    safe (a call that already has a linked task is skipped).
    """
    from storage.db import SessionLocal
    from services.missed_call_sync import sync_missed_calls_for_date

    db = SessionLocal()
    try:
        result = sync_missed_calls_for_date(db, target_date)
        if result["created"] or result["updated"]:
            print(f"[F1-worker] missed_call_sync {target_date}: {result}")
    finally:
        db.close()


LEAD_BACKFILL_LOCK = Lock()


def run_lead_call_backfill():
    """Retry linking recent Leads to their matching Call.

    See routes.flintdesk.leads.backfill_unlinked_leads: leads imported as a
    same-day call list are commonly matched to their call before that call
    has happened or been analysed, so the one-shot match at import time
    finds nothing and is never retried on its own. This periodically
    revisits any lead still unlinked so it catches up automatically once
    its call exists and has been analysed.
    """
    if not LEAD_BACKFILL_LOCK.acquire(blocking=False):
        print("[scheduler] Skipping lead_call_backfill: already running")
        return
    try:
        from storage.db import SessionLocal
        from routes.flintdesk.leads import backfill_unlinked_leads

        db = SessionLocal()
        try:
            result = backfill_unlinked_leads(db)
            if result["checked"]:
                print(f"[scheduler] lead_call_backfill: {result}")
        finally:
            db.close()
    finally:
        LEAD_BACKFILL_LOCK.release()


LEAD_SHEET_SYNC_LOCK = Lock()


def run_lead_sheet_sync():
    """Import new Google Sheet lead rows (see services/lead_sync)."""
    if not LEAD_SHEET_SYNC_LOCK.acquire(blocking=False):
        print("[scheduler] Skipping lead_sheet_sync: already running")
        return
    try:
        from storage.db import SessionLocal
        from services.lead_sync.lead_sync_processor import sync_leads
        from utils import config

        db = SessionLocal()
        try:
            sync_leads(db, start_date=config.LEAD_SHEET_SYNC_START_DATE)
            # Leads held back by the Teams interval are sent on the first pass after it ends, even when
            # this pass found nothing new.
            from services.lead_sync.notifier import notify_batched
            notify_batched()
        except Exception as exc:
            db.rollback()
            # Type only: the message could echo configuration values.
            print(f"[scheduler] lead_sheet_sync failed: {type(exc).__name__}")
            logger.exception("lead_sheet_sync failed")
        finally:
            db.close()
    finally:
        LEAD_SHEET_SYNC_LOCK.release()


PRIORITY_CASES_LOCK = Lock()


@retry_db_error(max_attempts=3, delay_seconds=2)
def run_priority_cases_worker():
    """Classify RFE / I-130 / I-485 / H4 / H4EAD for newly transcribed calls (services/priority_cases.py).

    Runs after F3 each minute as its OWN job, so it never lengthens or fails an F3 run: it reads the transcript F2 already
    stored (no STT), writes only call_priority_analysis, and uses F3's LLM client (shared rate limit, 429 back-off, credit
    circuit breaker) one request at a time in a small batch. It steps aside while F3 still has fresh transcripts to analyse.
    State-driven and idempotent: a restart only delays it, and an analysed transcript is never sent again.
    """
    from services import priority_cases as pc
    if not pc.enabled():
        return
    if not PRIORITY_CASES_LOCK.acquire(blocking=False):
        print("[scheduler] Skipping priority_cases_worker: already running")
        return
    try:
        from services.calls_page_scope import calls_page_call_ids
        from services.dependency_ai import f3_is_busy
        from services.Processing_pipeline.fd_llm_node import FDLLM
        from storage.db import SessionLocal

        def f3_busy():
            with SessionLocal() as db:
                return f3_is_busy(db)

        def listed(day):
            with SessionLocal() as db:
                return calls_page_call_ids(db, day)

        start, end = pc.worker_window(datetime.now(CT).date())
        counts = pc.run_batch(SessionLocal, FDLLM, start=start, end=end, limit=pc.WORKER_BATCH_LIMIT,
                              is_listed=listed, should_stop=f3_busy, reanalyse=False)
        if counts["eligible"] or counts.get("error"):
            print(f"[priority-cases] {counts}")
    except Exception as exc:  # noqa: BLE001 - the next run retries; never take the scheduler down
        print(f"[priority-cases] run failed: {type(exc).__name__}")
        logger.exception("priority_cases_worker failed")
    finally:
        PRIORITY_CASES_LOCK.release()


CASE_LOST_LOCK = Lock()


def run_case_lost_processor():
    """Move In-Transition calls past the Lost period to Lost and cut Teams batches (services/case_lost_processor.py).

    State-driven and idempotent: whatever is eligible when it runs is processed, so a restart or a missed run only delays
    it. Database locks make several instances safe; the in-process lock only avoids overlapping runs of this instance.
    """
    if not CASE_LOST_LOCK.acquire(blocking=False):
        print("[scheduler] Skipping case_lost_processor: already running")
        return
    try:
        from storage.db import SessionLocal
        from services import case_lost_processor as lost
        result = lost.process_lost(SessionLocal)
        if result.get("moved") or result.get("batches"):
            print(f"[scheduler] case_lost_processor: {result}")
    except Exception as exc:
        print(f"[scheduler] case_lost_processor failed: {type(exc).__name__}")
        logger.exception("case_lost_processor failed")
    finally:
        CASE_LOST_LOCK.release()


def run_case_lost_delivery():
    """Deliver pending Lost-case batches to the Teams workflow (LOST_CASE_TEAMS_WEBHOOK_URL)."""
    try:
        from storage.db import SessionLocal
        from services import case_lost_processor as lost
        result = lost.deliver_pending(SessionLocal)
        if result.get("sent") or result.get("failed"):
            print(f"[scheduler] case_lost_delivery: {result}")
    except Exception as exc:
        print(f"[scheduler] case_lost_delivery failed: {type(exc).__name__}")
        logger.exception("case_lost_delivery failed")


def run_call_count_snapshot():
    """Save the official end-of-day call counts (17:05 Central, working days).

    Independent of F1/F2/F3 (takes none of their locks) and idempotent: the
    database guarantees one snapshot per business date even if several
    replicas fire this job.
    """
    from services import call_count_snapshot as snapshots

    business_date = snapshots.business_today()
    if not snapshots.is_snapshot_day(business_date):
        print(f"[scheduler] call_count_snapshot: {business_date} is not a snapshot day")
        return
    result = snapshots.take_snapshot(business_date)
    print(f"[scheduler] call_count_snapshot: {result}")


def run_call_count_reconciliation():
    """Re-check the last week's closed snapshots against the live database."""
    from services import call_count_snapshot as snapshots

    print(f"[scheduler] call_count_reconciliation: {snapshots.reconcile_recent()}")


# ==================================================================
# SCHEDULER STARTUP
# ==================================================================
def start_scheduler():
    """
    Start APScheduler only once.

    Scheduler architecture:

        EVERY MINUTE
            ↓
           F1
            ↓
           DB

        INDEPENDENT WORKER
            ↓
           F2
            ↓
        transcript
            ↓
           DB

        INDEPENDENT WORKER
            ↓
           F3
            ↓
        fd_llm_node.py

        DAILY
            ↓
        AutoFlint F3
            ↓
        llm_node.py
    """

    if scheduler.running:
        print(
            "[scheduler] Scheduler already running."
        )
        return

    # ==================================================================
    # 1. DAILY SAFETY-NET SYNCHRONIZATION
    # ==================================================================
    scheduler.add_job(
        func=run_daily_sync,
        trigger=CronTrigger(
            hour=2,
            minute=0,
            second=0,
            timezone=CT,
        ),
        id="daily_sync",
        replace_existing=True,

        # Only one daily synchronization should run at a time.
        max_instances=1,

        # If a run is missed, combine it rather than creating
        # multiple queued executions.
        coalesce=True,

        misfire_grace_time=3600,
    )

    # ==================================================================
    # 2. F1 - EVERY MINUTE
    # ==================================================================
    #
    # IMPORTANT:
    #
    # F1 is now COMPLETELY independent.
    #
    # It does NOT call F2.
    # It does NOT call F3.
    #
    # Therefore F2/F3 cannot block F1.
    #
    # Example:
    #
    # 12:26 → F1
    # 12:27 → F1
    # 12:28 → F1
    #
    # even if F2 is still processing calls.
    #
    # max_instances=1 prevents two F1 executions from running
    # simultaneously against the database.
    # ==================================================================
    scheduler.add_job(
        func=run_f1_every_minute,
        trigger=CronTrigger(
            minute="*",
            second=0,
            timezone=CT,
        ),
        id="f1_every_minute",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )

    # ==================================================================
    # 3. F2 - INDEPENDENT STT WORKER
    # ==================================================================
    #
    # F2 checks the database every minute for transcript-pending
    # calls.
    #
    # It is independent from F1.
    #
    # If F2 takes longer than one minute:
    #
    #     F2 is still allowed to finish.
    #
    # The scheduler will NOT create another F2 instance because
    # max_instances=1.
    #
    # This prevents two STT workers from accidentally processing
    # the same call simultaneously.
    # ==================================================================
    scheduler.add_job(
        func=run_f2_worker,
        trigger=CronTrigger(
            minute="*",
            second=15,
            timezone=CT,
        ),
        id="f2_worker",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )

    # ==================================================================
    # 4. F3 - INDEPENDENT FLINTDESK LLM WORKER
    # ==================================================================
    #
    # F3 checks the database for transcript-ready calls.
    #
    # It does NOT wait for the complete F2 batch to finish.
    #
    # As soon as F2 commits a transcript, the next F3 worker cycle
    # can pick it up.
    #
    # FlintDesk explicitly uses:
    #
    #     F3Processor(application="flintdesk")
    #
    # which calls fd_llm_node.py.
    #
    # max_instances=1 prevents multiple LLM workers from processing
    # the same pending records simultaneously.
    # ==================================================================
    scheduler.add_job(
        func=run_flintdesk_f3_worker,
        trigger=CronTrigger(
            minute="*",
            second=30,
            timezone=CT,
        ),
        id="flintdesk_f3_worker",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )

    # Missed Call sync (dst = 602/2243860601 -> Missed Calls page) is not a
    # separate scheduled job -- it runs as the last step inside
    # run_f1_every_minute itself, right after F1 commits that tick's calls.

    # Bounded safety net for late S3 uploads and transient STT/LLM failures.
    scheduler.add_job(
        func=run_pipeline_fallback,
        trigger=CronTrigger(minute="*/15", second=45, timezone=CT),
        id="pipeline_fallback",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )

    # Leads imported ahead of the calls they describe (a same-day call list)
    # can't be linked to a call at import time; retry periodically so they
    # catch up automatically once the call happens and is analysed.
    scheduler.add_job(
        func=run_lead_call_backfill,
        trigger=CronTrigger(minute="*/5", second=50, timezone=CT),
        id="lead_call_backfill",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )

    # ==================================================================
    # 5. DAILY AUTOFLINT F3
    # ==================================================================
    #
    # AutoFlint remains completely separate from FlintDesk.
    #
    # It runs once every day at 2:05 AM Central.
    #
    # AutoFlint uses:
    #
    #     F3Processor(application="autoflint")
    #
    # which calls llm_node.py.
    # ==================================================================
    scheduler.add_job(
        func=run_daily_autoflint_analysis,
        trigger=CronTrigger(
            hour=2,
            minute=5,
            second=0,
            timezone=CT,
        ),
        id="daily_autoflint_f3",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # ==================================================================
    # 6. DAILY CALL-COUNT SNAPSHOT AND RECONCILIATION
    # ==================================================================
    # 17:05 Central (agents finish at 17:00), working days only. Both jobs use
    # the business timezone, so daylight-saving changes need no handling here.
    from services.call_count_snapshot import BUSINESS_TZ_NAME, snapshot_days_setting

    business_tz = pytz.timezone(BUSINESS_TZ_NAME)
    scheduler.add_job(
        func=run_call_count_snapshot,
        trigger=CronTrigger(day_of_week=snapshot_days_setting(), hour=17, minute=5, second=0, timezone=business_tz),
        id="call_count_snapshot",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        func=run_call_count_reconciliation,
        trigger=CronTrigger(hour=6, minute=30, second=0, timezone=business_tz),
        id="call_count_reconciliation",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # Case lifecycle: Lost processing (hourly, Central; off until CASE_LOST_PROCESSOR_ENABLED=true) and Teams delivery.
    from services.case_lost_processor import processor_enabled as _lost_enabled
    if _lost_enabled():
        scheduler.add_job(
            func=run_case_lost_processor,
            trigger=CronTrigger(minute=20, second=0, timezone=CT),
            id="case_lost_processor",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        if os.getenv("LOST_CASE_TEAMS_WEBHOOK_URL", "").strip():
            scheduler.add_job(
                func=run_case_lost_delivery,
                trigger=IntervalTrigger(minutes=2),
                id="case_lost_delivery",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=120,
            )

    # Priority Cases (RFE / I-130 / I-485 / H4 / H4EAD): its own minute job, offset from F1/F2/F3; PRIORITY_CASES_ENABLED=false turns it off.
    from services.priority_cases import enabled as _priority_cases_enabled
    if _priority_cases_enabled():
        scheduler.add_job(
            func=run_priority_cases_worker,
            trigger=CronTrigger(second=50, timezone=CT),
            id="priority_cases_worker",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )

    from utils import config as _config
    if _config.OPEN_CALL_DEPENDENCY_AI_ENABLED:
        scheduler.add_job(
            func=run_open_call_dependency_ai,
            trigger=IntervalTrigger(minutes=5),
            id="open_call_dependency_ai",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
    if _config.LEAD_SHEET_SYNC_ENABLED:
        scheduler.add_job(
            func=run_lead_sheet_sync,
            trigger=IntervalTrigger(minutes=5),
            id="lead_sheet_sync",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )

    # ==================================================================
    # START SCHEDULER
    # ==================================================================
    if os.getenv("ENABLE_SCHEDULER", "true").lower() == "true":
        scheduler.start()

        print("=" * 70)
        print("Scheduler started successfully.")
        print("Scheduler timezone :", scheduler.timezone)
        print("Current UTC        :", datetime.now(pytz.UTC))
        print("Current CT         :", datetime.now(CT))
        print("=" * 70)

        # --------------------------------------------------------------
        # Print all registered jobs and their next execution time.
        # This makes Render logs easy to verify. APScheduler only computes
        # next_run_time once the scheduler has actually started, so this
        # must stay inside the ENABLE_SCHEDULER guard above.
        # --------------------------------------------------------------
        for job in scheduler.get_jobs():
            print(
                f"Job '{job.id}' next run: "
                f"{job.next_run_time}"
            )

        print("=" * 70)
