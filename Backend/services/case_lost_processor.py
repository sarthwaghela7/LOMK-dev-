"""Automatic Lost processing and the durable Teams batch outbox.

A call that has been In-Transition for the Lost period (services/case_lifecycle.LOST_AFTER_DAYS, Central calendar days)
and has not reached Dropped / Success becomes Lost: its status becomes "<last In-Transition status> & Lost" and the
database trigger (migration 073) moves its stage to Lost and writes the lifecycle history.

Safety
  * one conditional UPDATE per batch, over rows locked with FOR UPDATE SKIP LOCKED: concurrent workers/instances never
    take the same call, and a save that committed first (status no longer In-Transition, or a new entry time) is seen
    by the UPDATE's own WHERE and skipped, so a stale job can never overwrite a newer status;
  * the transition rows (outbox) are inserted in the SAME transaction as the status change;
  * a transition is unique per (call, In-Transition entry): re-running changes nothing;
  * every ten unbatched transitions are cut into one batch row (ordered, under an advisory lock);
    batches are delivered afterwards with retries and an idempotency key (batch id).
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import text

from services import case_lifecycle as lifecycle

logger = logging.getLogger("flintdesk.case_lost")

BATCH_SIZE = 10
PROCESS_CHUNK = 200
_LOCK_PROCESS = 739412901
_LOCK_BATCH = 739412902

# Suffix appended exactly once, onto the last valid In-Transition status (never onto a value that already ends in & Lost).
_LOST_STATUS_SQL = """
    CASE WHEN lower(btrim(coalesce(a.last_in_transition_status, a.fd_m_case_status_change))) LIKE '% & lost'
         THEN coalesce(a.last_in_transition_status, a.fd_m_case_status_change)
         ELSE btrim(coalesce(a.last_in_transition_status, a.fd_m_case_status_change)) || ' & Lost' END
"""

_PICK_AND_MARK = text(f"""
    WITH picked AS (
        SELECT id, fd_m_case_status_change AS old_status, in_transition_entered_at AS entered
        FROM call_analysis_results
        WHERE case_lifecycle_stage = 'in_transition' AND in_transition_entered_at < :cutoff
        ORDER BY id LIMIT :chunk FOR UPDATE SKIP LOCKED
    )
    UPDATE call_analysis_results a
    SET fd_m_case_status_change = {_LOST_STATUS_SQL}
    FROM picked p
    WHERE a.id = p.id AND a.case_lifecycle_stage = 'in_transition' AND a.in_transition_entered_at = p.entered
      AND a.fd_m_case_status_change IS NOT DISTINCT FROM p.old_status
    RETURNING a.id, a.call_id::text AS call_id, p.old_status, a.fd_m_case_status_change AS new_status, p.entered
""")

_RECORD = text("""
    INSERT INTO fd_case_lost_transitions (analysis_id, call_id, call_reference, in_transition_entered_at, previous_status, lost_status)
    VALUES (:analysis_id, :call_id, (SELECT call_reference FROM calls WHERE call_id::text = :call_id), :entered, :old_status, :new_status)
    ON CONFLICT (analysis_id, in_transition_entered_at) DO NOTHING
""")

_HISTORY = text("""
    INSERT INTO fd_case_status_history (analysis_id, call_id, previous_status, new_status, changed_by)
    VALUES (:analysis_id, :call_id, :old_status, :new_status, NULL)
""")


def processor_enabled() -> bool:
    return os.getenv("CASE_LOST_PROCESSOR_ENABLED", "false").strip().lower() == "true"


def _cutoff(today):
    """Entry times strictly before this instant are eligible: entry date (Central) <= today - LOST_AFTER_DAYS."""
    day = lifecycle.entered_on_or_before(today) + timedelta(days=1)
    return datetime.combine(day, time.min, tzinfo=lifecycle.CENTRAL)


def count_eligible(db, today=None) -> int:
    return db.execute(text("""SELECT count(*) FROM call_analysis_results
        WHERE case_lifecycle_stage = 'in_transition' AND in_transition_entered_at < :cutoff"""),
        {"cutoff": _cutoff(today)}).scalar()


def process_lost(session_factory, today=None, *, chunk=PROCESS_CHUNK) -> dict:
    """Move every eligible call to Lost (chunked transactions), then cut and return the ready batches."""
    moved = 0
    cutoff = _cutoff(today)
    while True:
        with session_factory() as db, db.begin():
            if not db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _LOCK_PROCESS}).scalar():
                return {"moved": moved, "skipped": "another worker is processing", "batches": 0}
            rows = db.execute(_PICK_AND_MARK, {"cutoff": cutoff, "chunk": chunk}).mappings().all()
            for row in rows:
                db.execute(_RECORD, {"analysis_id": row["id"], "call_id": row["call_id"], "entered": row["entered"],
                                      "old_status": row["old_status"], "new_status": row["new_status"]})
                db.execute(_HISTORY, {"analysis_id": row["id"], "call_id": row["call_id"], "old_status": row["old_status"],
                                       "new_status": row["new_status"]})
        moved += len(rows)
        if len(rows) < chunk:
            break
    return {"moved": moved, "batches": cut_batches(session_factory)}


def cut_batches(session_factory, size=BATCH_SIZE) -> int:
    """Turn each full group of `size` unbatched transitions into one outbox batch. Returns the batches created."""
    created = 0
    while True:
        with session_factory() as db, db.begin():
            db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_BATCH})
            rows = db.execute(text("""SELECT id, analysis_id, call_id, call_reference, previous_status, lost_status, transitioned_at
                FROM fd_case_lost_transitions WHERE batch_id IS NULL ORDER BY id LIMIT :n FOR UPDATE"""), {"n": size}).mappings().all()
            if len(rows) < size:
                return created
            batch_id = uuid.uuid4()
            # sequence is assigned by the table; the payload is completed below so it can carry it.
            sequence = db.execute(text("SELECT nextval(pg_get_serial_sequence('fd_case_lost_batches', 'sequence'))")).scalar()
            payload = build_payload(batch_id, sequence, rows)
            db.execute(text("INSERT INTO fd_case_lost_batches (id, sequence, payload) VALUES (:id, :seq, CAST(:payload AS JSONB))"),
                       {"id": batch_id, "seq": sequence, "payload": _json(payload)})
            db.execute(text("UPDATE fd_case_lost_transitions SET batch_id = :b WHERE id = ANY(:ids)"),
                       {"b": batch_id, "ids": [row["id"] for row in rows]})
            created += 1


def build_payload(batch_id, sequence, rows) -> dict:
    """Event schema v1. Deliberately free of client names, numbers and call content."""
    return {
        "schema": "flintdesk.lost_cases.batch.v1",
        "batch_id": str(batch_id),
        "idempotency_key": str(batch_id),
        "sequence": int(sequence),
        "count": len(rows),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "calls": [{
            "call_id": row["call_id"],
            "call_reference": row["call_reference"],
            "previous_status": row["previous_status"],
            "lost_status": row["lost_status"],
            "lost_at": row["transitioned_at"].isoformat() if row["transitioned_at"] else None,
        } for row in rows],
    }


def _json(value) -> str:
    import json
    return json.dumps(value, default=str)


# ------------------------------------------------------------------ delivery
MAX_BACKOFF = timedelta(hours=6)


def teams_card(payload: dict) -> dict:
    """Adaptive Card for a Teams Workflows webhook (same envelope as the lead notification)."""
    lines = [{"type": "TextBlock", "wrap": True, "spacing": "Small",
              "text": f"**{call.get('call_reference') or call['call_id']}** — {call['lost_status']}"} for call in payload["calls"]]
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
                    "body": [{"type": "TextBlock", "weight": "Bolder", "size": "Medium",
                              "text": f"{payload['count']} more cases moved to Lost (batch {payload['sequence']})"}, *lines]}}],
        "flintdesk": payload}


def deliver_pending(session_factory, post=None, now=None, limit=20) -> dict:
    """Post due, undelivered batches to LOST_CASE_TEAMS_WEBHOOK_URL. At-least-once; the batch id is the idempotency key.

    Without the URL nothing is sent and batches simply stay pending (they can be read from fd_case_lost_batches).
    """
    url = os.getenv("LOST_CASE_TEAMS_WEBHOOK_URL", "").strip()
    if not url:
        return {"sent": 0, "failed": 0, "disabled": True}
    if post is None:
        import requests
        post = requests.post
    now = now or datetime.now(timezone.utc)
    sent = failed = 0
    with session_factory() as db, db.begin():
        batches = db.execute(text("""SELECT id, payload, attempts FROM fd_case_lost_batches
            WHERE delivered_at IS NULL AND next_attempt_at <= :now ORDER BY sequence LIMIT :n FOR UPDATE SKIP LOCKED"""),
            {"now": now, "n": limit}).mappings().all()
        for batch in batches:
            try:
                response = post(url, json=teams_card(batch["payload"]), headers={"Idempotency-Key": str(batch["id"])}, timeout=10)
                ok = response.status_code < 300
                error = None if ok else f"HTTP {response.status_code}"
            except Exception as exc:  # the message may embed the URL: keep the type only
                ok, error = False, type(exc).__name__
            if ok:
                db.execute(text("UPDATE fd_case_lost_batches SET delivered_at = :now, attempts = attempts + 1, last_error = NULL WHERE id = :id"),
                           {"now": now, "id": batch["id"]})
                sent += 1
            else:
                delay = min(MAX_BACKOFF, timedelta(minutes=2) * (2 ** batch["attempts"]))
                db.execute(text("""UPDATE fd_case_lost_batches SET attempts = attempts + 1, last_error = :err,
                    next_attempt_at = :next WHERE id = :id"""), {"err": error, "next": now + delay, "id": batch["id"]})
                failed += 1
    return {"sent": sent, "failed": failed}
