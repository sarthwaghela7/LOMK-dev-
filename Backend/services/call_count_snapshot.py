"""End-of-day call-count snapshots and their reconciliation.

What counts as "the call count" is NOT redefined here. It is the number the
Calls page shows ("<N> calls in range": analysed calls plus calls still waiting
for analysis), produced by the same two reads and row serialisers the page's
endpoints use (see compute_counts), so each call's Agent is the Agent column's
value. Dates are the Calls page's own business date.

At 17:05 America/Chicago the count is saved per agent, per extension and in
total, together with the ids of the calls that made it up. Snapshots are never
rewritten (database trigger). Later, the same count is recomputed from the
live database and compared with the snapshot: any per-agent or per-extension
difference is a MISMATCH even if the total is unchanged.

Persistence takes an explicit SQLAlchemy engine and an injectable ``compute``
so it can be tested against a throwaway PostgreSQL without the application
schema.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text

logger = logging.getLogger(__name__)

BUSINESS_TZ_NAME = "America/Chicago"
BUSINESS_TZ = ZoneInfo(BUSINESS_TZ_NAME)
SNAPSHOT_HOUR, SNAPSHOT_MINUTE = 17, 5  # agents work 9:00-17:00 CT
NA = "N/A"
TOTAL_KEY = "TOTAL"
DIMENSIONS = ("agent", "extension")
INFORMATIONAL_DIMENSIONS = ("extension",)
_LOCK_NAMESPACE = 739412807
_DEFAULT_DAYS = "mon-fri"
_DAY_NUMBERS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

MATCH = "MATCH"
MISMATCH = "MISMATCH"
NOT_SNAPSHOTTED = "NOT YET SNAPSHOTTED"
SNAPSHOT_FAILED = "SNAPSHOT FAILED"
RECONCILIATION_ERROR = "RECONCILIATION ERROR"
NOT_CHECKED = "NOT YET CHECKED"


@dataclass(frozen=True)
class Member:
    call_id: str
    call_reference: str
    agent: str
    extension: str


@dataclass
class Counts:
    total: int
    by_agent: dict[str, int]
    by_extension: dict[str, int]
    members: list[Member] = field(default_factory=list)


# --------------------------------------------------------------------------
# Business calendar (America/Chicago, DST-aware via zoneinfo -- no fixed offsets)
# --------------------------------------------------------------------------
def business_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(BUSINESS_TZ)).astimezone(BUSINESS_TZ)


def business_today(now: datetime | None = None) -> date:
    return business_now(now).date()


def snapshot_days_setting() -> str:
    """APScheduler-style day_of_week, e.g. "mon-fri" (env CALL_SNAPSHOT_DAY_OF_WEEK)."""
    value = os.getenv("CALL_SNAPSHOT_DAY_OF_WEEK", _DEFAULT_DAYS).strip().lower()
    return value if _parse_days(value) is not None else _DEFAULT_DAYS


def _parse_days(spec: str) -> set[int] | None:
    days: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            return None
        bounds = [piece.strip() for piece in part.split("-")]
        if len(bounds) > 2 or any(bound not in _DAY_NUMBERS for bound in bounds):
            return None
        start, end = _DAY_NUMBERS[bounds[0]], _DAY_NUMBERS[bounds[-1]]
        days.update(range(start, end + 1) if start <= end else [*range(start, 7), *range(0, end + 1)])
    return days


def is_snapshot_day(business_date: date, spec: str | None = None) -> bool:
    return business_date.weekday() in (_parse_days(spec or snapshot_days_setting()) or set())


def close_time(business_date: date) -> datetime:
    """The moment the day is considered closed (17:05 local, DST-correct)."""
    return datetime(business_date.year, business_date.month, business_date.day,
                    SNAPSHOT_HOUR, SNAPSHOT_MINUTE, tzinfo=BUSINESS_TZ)


# --------------------------------------------------------------------------
# Counting: reuse the existing canonical logic
# --------------------------------------------------------------------------
def compute_counts(db, business_date: date) -> Counts:
    """The number the Calls page shows for one day, grouped like its Agent / Extension columns.

    The Calls page header reads "<N> calls in range" where N is the analysed
    total (GET /calls/analysis, target_page="calls") plus the calls that have
    no analysis yet (GET /calls?only_unanalyzed=true). Those same two reads and
    the same row serialisers are used here, so the count and each row's Agent
    are exactly what the page displays. Nothing about that definition is
    changed.
    """
    from routes.calls import _effective_extension, _serialize_call_records, serialize_analysis_rows
    from services.analysis_read_query import analysis_read
    from sqlalchemy import func
    from storage.models import FDAssignment, FDUser

    rows, calls_by_id, _ = analysis_read(db, start=business_date, end=business_date,
                                         target_page="calls", summary_rows=True)
    analysed = serialize_analysis_rows(rows, db, include_raw=False, calls_by_id=calls_by_id,
                                       include_transcript=False, summary_rows=True)
    analysed_ids = {str(row.call_id) for row in rows if row.call_id}
    pending_calls, _, _ = analysis_read(db, start=business_date, end=business_date, call_source=True,
                                        target_page="calls", only_unanalyzed=True)
    pending_calls = [call for call in pending_calls if str(call.call_id) not in analysed_ids]
    pending = _serialize_call_records(db, pending_calls)
    pending_by_id = {str(call.call_id): call for call in pending_calls}

    by_agent: dict[str, int] = {}
    by_extension: dict[str, int] = {}
    members: list[Member] = []

    def add(call_id: str, reference: str, agent: str, call) -> None:
        agent = str(agent or "").strip()
        agent = NA if agent in ("", "-") else agent
        extension = str(_effective_extension(call) or "").strip() if call is not None else ""
        extension = extension or NA
        by_agent[agent] = by_agent.get(agent, 0) + 1
        by_extension[extension] = by_extension.get(extension, 0) + 1
        members.append(Member(call_id, reference, agent, extension))

    for row, item in zip(rows, analysed):
        # The Calls page keeps only rows the serialiser returns for it, in the same order.
        call = calls_by_id.get(str(row.call_id))
        call_id = str(row.call_id) if row.call_id else f"analysis:{row.id}"
        add(call_id, str((call.call_reference if call else "") or ""), item.get("front_desk_person_name"), call)
    for item in pending:
        call = pending_by_id.get(str(item.get("call_id")))
        add(str(item.get("call_id")), str(item.get("call_reference") or ""), item.get("assigned_agent_name"), call)

    # Registered agents / active extensions with no calls still appear (as 0).
    for (name,) in db.query(FDUser.name).filter(
        FDUser.is_active.is_(True), func.lower(func.trim(FDUser.role)) == "agent",
    ).all():
        if name and str(name).strip():
            by_agent.setdefault(str(name).strip(), 0)
    for (extension,) in db.query(FDAssignment.extension).filter(FDAssignment.is_active.is_(True)).distinct().all():
        if extension and str(extension).strip():
            by_extension.setdefault(str(extension).strip(), 0)
    by_agent.setdefault(NA, 0)
    return Counts(total=len(members), by_agent=by_agent, by_extension=by_extension, members=members)


def _compute_from_app_db(business_date: date) -> Counts:
    from storage.db import SessionLocal
    db = SessionLocal()
    try:
        return compute_counts(db, business_date)
    finally:
        db.close()


def _default_engine():
    from storage.db import engine
    return engine


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------
def take_snapshot(business_date: date, *, engine=None, compute=None, now: datetime | None = None) -> dict:
    """Create the official snapshot for ``business_date`` exactly once.

    Idempotent and safe to run concurrently: an existing completed snapshot is
    never touched. On failure no snapshot rows are written; the failure is
    recorded so it is visible and can be retried.
    """
    engine = engine or _default_engine()
    compute = compute or _compute_from_app_db
    now = business_now(now)
    logger.info("Daily call count snapshot started business_date=%s timezone=%s", business_date, BUSINESS_TZ_NAME)
    if business_date > now.date() or (business_date == now.date() and now < close_time(business_date)):
        logger.info("Daily call count snapshot skipped (day not closed) business_date=%s", business_date)
        return {"outcome": "not_closed", "business_date": business_date.isoformat()}
    try:
        with engine.connect() as conn:
            existing = conn.execute(text("SELECT status FROM fd_call_count_snapshot_runs WHERE business_date = :d"),
                                    {"d": business_date}).scalar()
        if existing == "completed":
            logger.info("Daily call count snapshot already exists business_date=%s", business_date)
            return {"outcome": "exists", "business_date": business_date.isoformat()}
        counts = compute(business_date)
        if not _save_snapshot(engine, business_date, counts, now):
            logger.info("Daily call count snapshot already exists business_date=%s", business_date)
            return {"outcome": "exists", "business_date": business_date.isoformat()}
        logger.info("Daily call count snapshot completed business_date=%s total_calls=%s agents=%s",
                    business_date, counts.total, len(counts.by_agent))
        return {"outcome": "created", "business_date": business_date.isoformat(),
                "total_calls": counts.total, "late": now.date() > business_date}
    except Exception as exc:  # noqa: BLE001 - recorded, never silently swallowed
        logger.exception("Daily call count snapshot failed business_date=%s", business_date)
        message = _short_error(exc)
        try:
            _record_failure(engine, business_date, message)
        except Exception:  # noqa: BLE001
            logger.exception("Could not record snapshot failure business_date=%s", business_date)
        return {"outcome": "failed", "business_date": business_date.isoformat(), "error": message}


def _short_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {' '.join(str(exc).split())}"[:300]


def _save_snapshot(engine, business_date: date, counts: Counts, now: datetime) -> bool:
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:ns, :k)"),
                     {"ns": _LOCK_NAMESPACE, "k": business_date.toordinal()})
        status = conn.execute(text("SELECT status FROM fd_call_count_snapshot_runs WHERE business_date = :d FOR UPDATE"),
                              {"d": business_date}).scalar()
        if status == "completed":
            return False
        conn.execute(text("""
            INSERT INTO fd_call_count_snapshot_runs
              (business_date, timezone, status, snapshot_at, late, total_calls)
            VALUES (:d, :tz, 'completed', :at, :late, :total)
            ON CONFLICT (business_date) DO UPDATE SET
              status = 'completed', timezone = EXCLUDED.timezone, snapshot_at = EXCLUDED.snapshot_at,
              late = EXCLUDED.late, total_calls = EXCLUDED.total_calls, error = NULL,
              attempts = fd_call_count_snapshot_runs.attempts + 1, updated_at = now()
            WHERE fd_call_count_snapshot_runs.status = 'failed'
        """), {"d": business_date, "tz": BUSINESS_TZ_NAME, "at": now,
               "late": now.date() > business_date, "total": counts.total})
        rows = [{"d": business_date, "dim": "total", "key": TOTAL_KEY, "n": counts.total}]
        rows += [{"d": business_date, "dim": "agent", "key": key, "n": n} for key, n in counts.by_agent.items()]
        rows += [{"d": business_date, "dim": "extension", "key": key, "n": n} for key, n in counts.by_extension.items()]
        conn.execute(text("""INSERT INTO fd_call_count_snapshots (business_date, dimension, key, call_count)
                             VALUES (:d, :dim, :key, :n)"""), rows)
        if counts.members:
            conn.execute(text("""INSERT INTO fd_call_count_snapshot_calls
                                   (business_date, call_id, call_reference, agent, extension)
                                 VALUES (:d, :call_id, :ref, :agent, :ext)"""),
                         [{"d": business_date, "call_id": m.call_id, "ref": m.call_reference,
                           "agent": m.agent, "ext": m.extension} for m in counts.members])
    return True


def _record_failure(engine, business_date: date, message: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO fd_call_count_snapshot_runs (business_date, timezone, status, error)
            VALUES (:d, :tz, 'failed', :error)
            ON CONFLICT (business_date) DO UPDATE SET
              error = EXCLUDED.error, attempts = fd_call_count_snapshot_runs.attempts + 1, updated_at = now()
            WHERE fd_call_count_snapshot_runs.status = 'failed'
        """), {"d": business_date, "tz": BUSINESS_TZ_NAME, "error": message})


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------
def compare(snapshot: dict[str, dict[str, int]], current: Counts) -> dict:
    """Pure comparison. A key missing on one side counts as 0 there.

    The official per-person count is the Agent column, so the overall status
    depends on the total and every agent. Extension rows are compared and shown
    for investigation (``informational``) but never decide MATCH/MISMATCH.
    """
    current_by_dim = {"total": {TOTAL_KEY: current.total}, "agent": current.by_agent, "extension": current.by_extension}
    rows: list[dict] = []
    for dimension in ("total", *DIMENSIONS):
        before, after = snapshot.get(dimension, {}), current_by_dim[dimension]
        keys = sorted(set(before) | set(after), key=lambda key: (key == NA, str(key)))
        for key in keys:
            was, now_ = int(before.get(key, 0)), int(after.get(key, 0))
            rows.append({"dimension": dimension, "key": key, "snapshot": was, "current": now_,
                         "difference": now_ - was, "status": MATCH if was == now_ else MISMATCH,
                         "informational": dimension in INFORMATIONAL_DIMENSIONS})
    mismatches = sum(1 for row in rows if row["status"] == MISMATCH and not row["informational"])
    return {"status": MISMATCH if mismatches else MATCH, "mismatches": mismatches, "rows": rows}


def _load_snapshot(conn, business_date: date) -> dict[str, dict[str, int]]:
    snapshot: dict[str, dict[str, int]] = {}
    for dimension, key, count in conn.execute(text(
        "SELECT dimension, key, call_count FROM fd_call_count_snapshots WHERE business_date = :d"), {"d": business_date}):
        snapshot.setdefault(dimension, {})[key] = count
    return snapshot


def _get_run(conn, business_date: date):
    return conn.execute(text("""SELECT business_date, status, snapshot_at, late, total_calls, attempts, error,
                                       last_checked_at, last_check_status, last_check_mismatches
                                FROM fd_call_count_snapshot_runs WHERE business_date = :d"""),
                        {"d": business_date}).mappings().first()


def reconcile_date(business_date: date, *, engine=None, compute=None) -> dict:
    """Compare the saved snapshot with the live database. Never reports MATCH on error."""
    engine = engine or _default_engine()
    compute = compute or _compute_from_app_db
    base = {"business_date": business_date.isoformat(), "timezone": BUSINESS_TZ_NAME}
    try:
        with engine.connect() as conn:
            run = _get_run(conn, business_date)
            if run is None:
                return {**base, "status": NOT_SNAPSHOTTED, "rows": []}
            if run["status"] == "failed":
                return {**base, "status": SNAPSHOT_FAILED, "error": run["error"], "attempts": run["attempts"], "rows": []}
            snapshot = _load_snapshot(conn, business_date)
        result = compare(snapshot, compute(business_date))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Call count reconciliation error business_date=%s", business_date)
        message = _short_error(exc)
        _store_check(engine, business_date, "ERROR", None, message)
        return {**base, "status": RECONCILIATION_ERROR, "error": message, "rows": []}
    for row in result["rows"]:
        if row["status"] == MISMATCH and not row["informational"]:
            logger.warning("Call count mismatch detected business_date=%s dimension=%s key=%s "
                           "snapshot_count=%s current_count=%s difference=%s", business_date,
                           row["dimension"], row["key"], row["snapshot"], row["current"], row["difference"])
    _store_check(engine, business_date, result["status"], result["mismatches"], None)
    with engine.connect() as conn:
        run = _get_run(conn, business_date)
    return {**base, **result, "snapshot_at": run["snapshot_at"].isoformat() if run and run["snapshot_at"] else None,
            "late": bool(run and run["late"])}


def _store_check(engine, business_date: date, status: str, mismatches: int | None, error: str | None) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text("""UPDATE fd_call_count_snapshot_runs
                                 SET last_checked_at = now(), last_check_status = :status,
                                     last_check_mismatches = :mm, last_check_error = :error, updated_at = now()
                                 WHERE business_date = :d AND status = 'completed'"""),
                         {"d": business_date, "status": status, "mm": mismatches, "error": error})
    except Exception:  # noqa: BLE001 - recording the result must not hide the result
        logger.exception("Could not store reconciliation result business_date=%s", business_date)


def reconcile_recent(days: int = 7, *, engine=None, compute=None, now: datetime | None = None) -> dict:
    """Re-check every completed snapshot from the last ``days`` closed days."""
    engine = engine or _default_engine()
    today = business_today(now)
    with engine.connect() as conn:
        dates = [row[0] for row in conn.execute(text("""
            SELECT business_date FROM fd_call_count_snapshot_runs
            WHERE status = 'completed' AND business_date >= :start AND business_date < :today
            ORDER BY business_date"""), {"start": today - timedelta(days=days), "today": today})]
    summary = {"checked": 0, "match": 0, "mismatch": 0, "error": 0}
    for business_date in dates:
        status = reconcile_date(business_date, engine=engine, compute=compute)["status"]
        summary["checked"] += 1
        summary["match" if status == MATCH else "mismatch" if status == MISMATCH else "error"] += 1
    logger.info("Call count reconciliation finished checked=%s match=%s mismatch=%s error=%s",
                summary["checked"], summary["match"], summary["mismatch"], summary["error"])
    return summary


# --------------------------------------------------------------------------
# Investigation helpers
# --------------------------------------------------------------------------
def affected_calls(business_date: date, *, agent: str | None = None, extension: str | None = None,
                   engine=None, compute=None) -> dict:
    """Exact calls behind a mismatch: added, no longer counted, or re-attributed."""
    engine = engine or _default_engine()
    compute = compute or _compute_from_app_db
    with engine.connect() as conn:
        run = _get_run(conn, business_date)
        if run is None or run["status"] != "completed":
            return {"status": NOT_SNAPSHOTTED if run is None else SNAPSHOT_FAILED, "calls": []}
        before = {row["call_id"]: row for row in conn.execute(text(
            """SELECT call_id, call_reference, agent, extension
               FROM fd_call_count_snapshot_calls WHERE business_date = :d"""), {"d": business_date}).mappings()}
    after = {member.call_id: member for member in compute(business_date).members}
    calls = []
    for call_id in sorted(set(before) | set(after)):
        old, new = before.get(call_id), after.get(call_id)
        if old and new:
            if old["agent"] == new.agent and old["extension"] == new.extension:
                continue
            change = "Re-attributed after snapshot"
        else:
            change = "Added after snapshot" if new else "No longer counted"
        agents = {old["agent"] if old else None, new.agent if new else None}
        extensions = {old["extension"] if old else None, new.extension if new else None}
        if (agent and agent not in agents) or (extension and extension not in extensions):
            continue
        calls.append({"call_id": call_id, "call_reference": (new.call_reference if new else old["call_reference"]),
                      "change": change,
                      "snapshot_agent": old["agent"] if old else None, "current_agent": new.agent if new else None,
                      "snapshot_extension": old["extension"] if old else None,
                      "current_extension": new.extension if new else None})
    return {"status": "OK", "calls": calls}


def history(*, days: int = 45, engine=None, now: datetime | None = None) -> list[dict]:
    """Stored result per business date, newest first. No recomputation (cheap)."""
    engine = engine or _default_engine()
    now = business_now(now)
    today = now.date()
    with engine.connect() as conn:
        runs = {row["business_date"]: row for row in conn.execute(text("""
            SELECT business_date, status, snapshot_at, late, total_calls, attempts, error,
                   last_checked_at, last_check_status, last_check_mismatches
            FROM fd_call_count_snapshot_runs WHERE business_date >= :start"""),
            {"start": today - timedelta(days=days)}).mappings()}
    items = []
    for offset in range(days + 1):
        day = today - timedelta(days=offset)
        run = runs.get(day)
        if run is None:
            if not is_snapshot_day(day) or now < close_time(day):
                continue
            items.append({"business_date": day.isoformat(), "status": NOT_SNAPSHOTTED})
            continue
        if run["status"] == "failed":
            status = SNAPSHOT_FAILED
        else:
            status = {"MATCH": MATCH, "MISMATCH": MISMATCH, "ERROR": RECONCILIATION_ERROR}.get(
                run["last_check_status"], NOT_CHECKED)
        items.append({"business_date": day.isoformat(), "status": status,
                      "snapshot_at": run["snapshot_at"].isoformat() if run["snapshot_at"] else None,
                      "late": bool(run["late"]), "total_calls": run["total_calls"],
                      "mismatches": run["last_check_mismatches"], "error": run["error"],
                      "last_checked_at": run["last_checked_at"].isoformat() if run["last_checked_at"] else None})
    return items
