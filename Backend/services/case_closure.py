"""Cross-agent case closure synchronisation.

When an agent saves Dropped / Success - GE / Success - PC, other agents' open logs for the SAME client
and the SAME case context are offered the closure through a persistent notification. Only when that
agent clicks Confirm are the logs listed in that notification set to the closure's status (through the
same history table the Calls page uses) and linked to the closure for traceability; X changes nothing.
If the closer later reopens the matter, logs still carrying the status confirmation set are restored.

Everything for one closing event (event row, notifications) is written in the caller's transaction, under
a per-client advisory lock, with unique indexes making retries and races harmless.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

logger = logging.getLogger("flintdesk.case_closure")

from services.case_lifecycle import spellings_of

# Cross-agent closure covers calls from 1 September 2026 onward. Earlier logs are never offered and a
# closure on an earlier log never notifies anyone. Override with CASE_CLOSURE_SINCE=YYYY-MM-DD.
import os
from datetime import date


def closure_start_date() -> date:
    try:
        return date.fromisoformat(os.getenv("CASE_CLOSURE_SINCE", "2026-09-01"))
    except ValueError:
        return date(2026, 9, 1)

# Keys: lower-cased stored spellings (the new dropdown wording and the legacy hyphenated one); values: the wording a
# closure is saved and confirmed with.
CLOSURE_STATUSES = {"dropped": "Dropped", "success ge": "Success GE", "success pc": "Success PC",
                    "success - ge": "Success GE", "success - pc": "Success PC"}
NOTIFICATION_CLOSED = "case_closed_by_another_agent"
NOTIFICATION_REVERSED = "case_closure_reversed"

# ---------------------------------------------------------------- statuses
def closure_status_of(value) -> str | None:
    """Canonical closure status for a stored value, or None when it is not a closure."""
    return CLOSURE_STATUSES.get(" ".join(str(value or "").lower().split()))


# ---------------------------------------------------------------- case context
# fd_a_case_type is free text written by the AI. Spellings of one matter ("H-1B", "H-1B visa", "H-1B
# extension") are folded into one family; generic text ("Immigration", "visa") is NOT a context, so it
# never matches anything. Two logs are the same context only when both have the same specific family.
_FAMILIES = (
    ("h-4", r"\bh[\s-]?4\b"), ("h-1b", r"\bh[\s-]?1[\s-]?b\b"),
    ("i-485 / green card", r"\bi[\s-]?485\b|green\s*card|adjustment of status|\bgc\b"),
    ("i-130", r"\bi[\s-]?130\b"), ("f-1 / opt / stem", r"\bf[\s-]?1\b|\bopt\b|\bstem\b|\bcpt\b"),
    ("o-1", r"\bo[\s-]?1\b"), ("l-1", r"\bl[\s-]?1\b"),
    ("eb / perm / i-140", r"\beb[\s-]?\d|\bperm\b|\bi[\s-]?140\b"),
    ("citizenship", r"citizenship|naturali[sz]ation|\bn[\s-]?400\b"),
    ("rfe / noid", r"\brfe\b|\bnoid\b"), ("asylum", r"asylum"),
    ("visa stamping / b-1 / b-2", r"\bb[\s-]?[12]\b|stamping|consular"),
)
_GENERIC = re.compile(
    r"^(immigration( (consultation|inquiry|enquiry|matter|case|services?|law|question|help))?|visas?|consultation|"
    r"general( inquiry| enquiry)?|n/?a|none|unknown|other|legal( advice)?|inquiry|enquiry)$", re.I)


def context_key(*values) -> tuple[str, str] | None:
    """(family key, display text) of the first specific case type among `values`, else None."""
    for value in values:
        shown = " ".join(str(value or "").split())
        if not shown:
            continue
        lowered = shown.lower()
        for name, pattern in _FAMILIES:
            if re.search(pattern, lowered):
                return name, shown
        if not _GENERIC.match(lowered) and len(lowered) >= 4:
            return "other:" + re.sub(r"[^a-z0-9 ]", "", lowered), shown
    return None


# ---------------------------------------------------------------- helpers
def _lock_client(db, client_id: str) -> None:
    """Serialise closure work per client (released at commit/rollback)."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"case_closure:{client_id}"})


def _now():
    return datetime.now(timezone.utc)


def default_owner_resolver(db, rows):
    """{analysis_id: (fd_user_id, agent_name)} for the logs `rows`, using the Calls page's own ownership rule.

    The extension assignment decides; a typed agent name only claims logs with no assigned owner.
    Logs whose owner cannot be resolved get no entry (and therefore no notification).
    """
    from routes.calls import assignment_agent_details
    from storage.models import Call, FDUser

    call_ids = [row["call_id"] for row in rows]
    calls = db.query(Call).filter(Call.call_id.in_(call_ids)).all() if call_ids else []
    by_call = assignment_agent_details(db, calls)
    users = {str(u.name or "").strip().casefold(): u for u in db.query(FDUser).filter(FDUser.is_active.is_(True)).all()}
    owners = {}
    for row in rows:
        detail = by_call.get(str(row["call_id"]))
        if detail and detail[0]:
            owners[row["analysis_id"]] = (str(detail[0]), detail[1])
            continue
        typed = users.get(str(row.get("front_desk_person_name") or "").strip().casefold())
        if typed:
            owners[row["analysis_id"]] = (str(typed.id), typed.name)
    return owners


_CLIENT_LOGS = text("""
    SELECT a.id AS analysis_id, c.call_id::text AS call_id, c.call_reference, c.call_date,
           a.fd_a_case_type, a.case_type, a.fd_m_case_status_change AS status,
           a.front_desk_person_name
    FROM calls c
    JOIN call_analysis_results a ON a.call_id::text = c.call_id::text
    WHERE c.client_id = :client AND a.id <> :closing
      AND c.call_date >= :since
      AND NOT EXISTS (SELECT 1 FROM fd_case_closure_links l WHERE l.analysis_id = a.id AND l.reverted_at IS NULL)
""")


def _eligible_rows(db, client_id, closing_analysis_id, key):
    """The client's other still-open logs whose case context is `key` (read-only; owners not resolved yet).

    Open = any status except Dropped / Success - GE / Success - PC; logs already linked to a closure, of another
    context, with no usable context or from before the rollout date are left out.
    """
    rows = [dict(r._mapping) for r in db.execute(_CLIENT_LOGS, {"client": client_id, "closing": closing_analysis_id,
                                                                  "since": closure_start_date()})]
    eligible = []
    for row in rows:
        if closure_status_of(row["status"]):      # open = any status except Dropped / Success - GE / Success - PC
            continue
        found = context_key(row["fd_a_case_type"], row["case_type"])
        if not found or found[0] != key:
            continue
        row["context"] = found[1]
        eligible.append(row)
    return eligible


def _log_summary(row, agent_name=None):
    return {
        "analysis_id": row["analysis_id"], "call_id": row["call_id"], "call_reference": row["call_reference"],
        "call_date": row["call_date"].isoformat() if row["call_date"] else None,
        "status": row["status"], "case_context": row["context"],
        **({"agent_name": agent_name} if agent_name is not None else {}),
    }


def find_candidates(db, *, client_id, closing_analysis_id, closing_user_id, key, owner_resolver=None):
    """Other agents' still-open logs of this client with the same case context, grouped by owner.

    Returns {owner_user_id: {"agent_name": str, "logs": [log dict, ...]}}. Logs closed independently
    (own Dropped / Success - GE / Success - PC status), already linked, of another context, with no usable context
    or owned by the closing agent are excluded. Read-only.
    """
    eligible = _eligible_rows(db, client_id, closing_analysis_id, key)
    if not eligible:
        return {}
    owners = (owner_resolver or default_owner_resolver)(db, eligible)
    grouped: dict[str, dict] = {}
    for row in eligible:
        owner = owners.get(row["analysis_id"])
        if not owner or str(owner[0]) == str(closing_user_id):
            continue
        group = grouped.setdefault(str(owner[0]), {"agent_name": owner[1], "logs": []})
        group["logs"].append(_log_summary(row))
    return grouped


def find_own_related(db, *, client_id, closing_analysis_id, user_id, key, owner_resolver=None):
    """The closing agent's OWN other open logs of this client with the same case context (read-only).

    The complement of find_candidates: only logs the Calls page's ownership rule assigns to `user_id` are returned,
    so an agent can never be offered (or close) a log that belongs to someone else.
    """
    eligible = _eligible_rows(db, client_id, closing_analysis_id, key)
    if not eligible:
        return []
    owners = (owner_resolver or default_owner_resolver)(db, eligible)
    return [_log_summary(row, owners[row["analysis_id"]][1]) for row in eligible
            if row["analysis_id"] in owners and str(owners[row["analysis_id"]][0]) == str(user_id)]


def _client_display(db, client_id):
    row = db.execute(text("SELECT client_name, phone_number FROM clients WHERE client_id = :c"), {"c": client_id}).first()
    return (row[0] or "", row[1] or "") if row else ("", "")


# ---------------------------------------------------------------- closing event
def on_status_saved(db, analysis, call, user, previous, new, *, owner_resolver=None):
    """Hook for every status save, inside the save's own transaction.

    A new closure status opens (or refreshes) the closure event and notifies matching agents; moving a
    closing record away from a closure status reverts it. Never raises: a failure rolls back only its own
    savepoint, so the agent's save still succeeds and no partial synchronisation is left behind.
    """
    new_closure, old_closure = closure_status_of(new), closure_status_of(previous)
    if not new_closure and not old_closure:
        return None
    if str(previous or "").strip() == str(new or "").strip():
        return None
    try:
        with db.begin_nested():
            if new_closure:
                return open_closure(db, analysis, call, user, new_closure, owner_resolver=owner_resolver)
            return revert_closure(db, analysis.id, user)
    except Exception:  # noqa: BLE001 - see docstring
        logger.exception("case closure sync failed for analysis %s; status save kept", getattr(analysis, "id", None))
        return None


def open_closure(db, analysis, call, user, status, *, owner_resolver=None):
    client_id = getattr(call, "client_id", None)
    found = context_key(getattr(analysis, "fd_a_case_type", None), getattr(analysis, "case_type", None))
    if not client_id or not found:
        return None  # no client identity or no specific context: nothing can be synchronised
    call_day = getattr(call, "call_date", None)
    if call_day is not None and call_day < closure_start_date():
        return None  # before the rollout start date (1 September 2026 by default)
    key, shown = found
    _lock_client(db, client_id)
    closer_id = (user or {}).get("fd_user_id")
    closer_name = (user or {}).get("name") or ""
    existing = db.execute(text(
        "SELECT id FROM fd_case_closures WHERE closing_analysis_id = :a AND reverted_at IS NULL"), {"a": analysis.id}).first()
    if existing:
        # Same record re-saved (possibly with another closure status): keep the original closer and event.
        db.execute(text("UPDATE fd_case_closures SET closure_status = :s WHERE id = :i"), {"s": status, "i": existing[0]})
        return {"closure_id": str(existing[0]), "notified": 0, "existing": True}
    closure_id = uuid.uuid4()
    db.execute(text("""
        INSERT INTO fd_case_closures (id, client_id, closing_analysis_id, closing_call_id, closing_user_id,
                                      closing_agent_name, closure_status, context_key, case_context, closed_at)
        VALUES (:id, :client, :analysis, :call, :user, :name, :status, :key, :context, :now)
    """), {"id": closure_id, "client": client_id, "analysis": analysis.id, "call": getattr(call, "call_id", None),
           "user": closer_id, "name": closer_name, "status": status, "key": key, "context": shown, "now": _now()})
    groups = find_candidates(db, client_id=client_id, closing_analysis_id=analysis.id, closing_user_id=closer_id,
                             key=key, owner_resolver=owner_resolver)
    client_name, client_number = _client_display(db, client_id)
    notified = 0
    for owner_id, group in groups.items():
        context = {
            "closure_id": str(closure_id), "closing_agent_name": closer_name, "closure_status": status,
            "client_id": client_id, "client_name": client_name, "client_number": client_number,
            "case_context": shown, "closed_at": _now().isoformat(), "logs": group["logs"],
        }
        result = db.execute(text("""
            INSERT INTO task_notifications (id, recipient_id, title, message, notification_type, source_page,
                                            entity_id, change_context, created_at, closure_id)
            VALUES (:id, :recipient, :title, :message, :type, 'calls', :entity, CAST(:ctx AS JSONB), :now, :closure)
            ON CONFLICT (closure_id, recipient_id, notification_type) WHERE closure_id IS NOT NULL DO NOTHING
        """), {"id": uuid.uuid4(), "recipient": owner_id, "title": "Case Closed by Another Agent",
               "message": closure_message(context), "type": NOTIFICATION_CLOSED, "entity": str(closure_id),
               "ctx": _json(context), "now": _now(), "closure": closure_id})
        notified += result.rowcount or 0
    return {"closure_id": str(closure_id), "notified": notified, "existing": False}


def closure_message(ctx: dict) -> str:
    who = f"{ctx.get('client_name')} ({ctx.get('client_number')})" if ctx.get("client_name") else str(ctx.get("client_number") or ctx.get("client_id"))
    return (f"{ctx['closing_agent_name']} marked the {ctx['case_context']} matter for client {who} as {ctx['closure_status']}. "
            "If this is the same matter as your call log(s), confirm and they will be set to "
            f"{ctx['closure_status']} (they stop counting as open). Close this with X to leave them unchanged.")


def _json(value) -> str:
    import json
    return json.dumps(value, default=str)


# ---------------------------------------------------------------- confirm / dismiss
def _own_notification(db, notification_id, user_id):
    return db.execute(text("""
        SELECT id, closure_id, change_context, dismissed_at, confirmed_at, notification_type
        FROM task_notifications WHERE id = :id AND recipient_id = :user AND closure_id IS NOT NULL FOR UPDATE
    """), {"id": notification_id, "user": user_id}).mappings().first()


def _set_case_status(db, analysis_id, call_id, previous, new_status, user_id, *, set_date=True):
    """Write a case status the way the Calls page does: status, its date, history row, manual score reset.

    The status-save hook (on_status_saved) is deliberately NOT called: a confirmation must never start a
    second closure event that notifies other agents in turn.
    """
    from services.stage_completion import case_status_change_date
    db.execute(text("""
        UPDATE call_analysis_results
        SET fd_m_case_status_change = :s, fd_m_date_entry = :d,
            manual_data_score = NULL, manual_data_score_reason = NULL, manual_data_scored_at = NULL
        WHERE id = :a
    """), {"s": new_status, "d": case_status_change_date() if set_date and new_status else None, "a": analysis_id})
    # Restoring a log that had NO status before the closure is a cleared status: like the normal save path, which never
    # records a blank status, no history row is written (new_status is NOT NULL in fd_case_status_history).
    if new_status:
        db.execute(text("""
            INSERT INTO fd_case_status_history (analysis_id, call_id, previous_status, new_status, changed_by)
            VALUES (:a, CAST(:c AS UUID), :p, :n, :u)
        """), {"a": analysis_id, "c": str(call_id) if call_id else None, "p": previous, "n": new_status, "u": user_id})


def confirm_notification(db, notification_id, user_id, *, owner_resolver=None):
    """The recipient confirms: set their own still-open logs listed in this notification to the closure status.

    Each log is re-checked at confirm time (still this recipient's, same client, still open); anything stale is
    skipped and reported, never touched. Idempotent: a second confirm changes nothing.

    Returns {"state": "confirmed"|"already_confirmed"|"reverted"|"dismissed"|"not_found", "updated": [...],
    "skipped": [...], "closure_status", "closed_by"}.
    """
    empty = {"updated": [], "skipped": []}
    note = _own_notification(db, notification_id, user_id)
    if not note:
        return {"state": "not_found", **empty}
    if note["confirmed_at"]:
        return {"state": "already_confirmed", **empty}
    if note["dismissed_at"]:
        return {"state": "dismissed", **empty}
    closure = db.execute(text(
        "SELECT client_id, reverted_at, closure_status, closing_agent_name, case_context, closed_at "
        "FROM fd_case_closures WHERE id = :i"),
        {"i": note["closure_id"]}).first()
    if not closure:
        return {"state": "not_found", **empty}
    _lock_client(db, closure[0])
    now = _now()
    if closure[1] is not None:  # the closer reopened it meanwhile
        db.execute(text("UPDATE task_notifications SET dismissed_at = :n, read_at = COALESCE(read_at, :n) WHERE id = :id"),
                   {"n": now, "id": notification_id})
        return {"state": "reverted", **empty}
    client_id, closure_status, closer = closure[0], closure[2], closure[3]
    updated, skipped = [], []
    for log in (note["change_context"] or {}).get("logs", []):
        ref = log.get("call_reference") or log.get("call_id")
        row = db.execute(text("""
            SELECT a.id, a.fd_m_case_status_change, a.front_desk_person_name, c.call_id::text AS call_id, c.client_id
            FROM call_analysis_results a JOIN calls c ON c.call_id::text = a.call_id::text WHERE a.id = :a FOR UPDATE OF a
        """), {"a": log["analysis_id"]}).mappings().first()
        if not row or row["client_id"] != client_id:
            skipped.append({"call_reference": ref, "reason": "Call record not found for this client."})
            continue
        owner = (owner_resolver or default_owner_resolver)(db, [{"analysis_id": row["id"], "call_id": row["call_id"],
                                                                  "front_desk_person_name": row["front_desk_person_name"]}]).get(row["id"])
        if not owner or str(owner[0]) != str(user_id):
            skipped.append({"call_reference": ref, "reason": "This call record is not assigned to you."})
            continue
        if closure_status_of(row["fd_m_case_status_change"]):
            skipped.append({"call_reference": ref, "reason": f"Already {row['fd_m_case_status_change']}."})
            continue
        _set_case_status(db, row["id"], row["call_id"], row["fd_m_case_status_change"], closure_status, user_id)
        db.execute(text("""
            INSERT INTO fd_case_closure_links (id, closure_id, analysis_id, call_id, agent_user_id, confirmed_at, confirmed_by)
            VALUES (:id, :closure, :analysis, CAST(:call AS UUID), :agent, :now, :agent)
            ON CONFLICT DO NOTHING
        """), {"id": uuid.uuid4(), "closure": note["closure_id"], "analysis": row["id"], "call": row["call_id"],
               "agent": user_id, "now": now})
        # call_id (the call's unique id) lets the client open exactly this call record after Confirm;
        # fd_m_date_entry lets it show the new Case Status date without reloading.
        date_entry = db.execute(text("SELECT fd_m_date_entry FROM call_analysis_results WHERE id = :a"), {"a": row["id"]}).scalar()
        updated.append({"call_reference": ref, "previous_status": row["fd_m_case_status_change"], "new_status": closure_status,
                        "call_id": row["call_id"], "analysis_id": row["id"], "call_date": log.get("call_date"),
                        "fd_m_date_entry": date_entry.isoformat() if date_entry else None})
    db.execute(text("""
        UPDATE task_notifications SET confirmed_at = :n, dismissed_at = :n, read_at = COALESCE(read_at, :n) WHERE id = :id
    """), {"n": now, "id": notification_id})
    client_name, client_number = _client_display(db, client_id)
    return {"state": "confirmed", "updated": updated, "skipped": skipped, "closure_status": closure_status, "closed_by": closer,
            # Same shape as inherited_closures(): lets the Calls table show "<agent> changed to ..." at once.
            "closure": {"closure_id": str(note["closure_id"]), "closing_agent_name": closer, "closure_status": closure_status,
                        "case_context": closure[4], "client_id": client_id, "client_name": client_name,
                        "client_number": client_number, "closed_at": closure[5].isoformat() if closure[5] else None}}


def dismiss_notification(db, notification_id, user_id):
    """The recipient closes the notification with X: nothing is linked and it never comes back."""
    note = _own_notification(db, notification_id, user_id)
    if not note:
        return {"state": "not_found"}
    if not note["dismissed_at"]:
        db.execute(text("UPDATE task_notifications SET dismissed_at = :n, read_at = COALESCE(read_at, :n) WHERE id = :id"),
                   {"n": _now(), "id": notification_id})
    return {"state": "dismissed"}


def pending_notifications(db, user_id):
    """The recipient's undismissed closure notifications, oldest first (a queue).

    Fails safe (empty list) until migrations 071/072 are applied, so every user's poll stays quiet.
    """
    try:
        with db.begin_nested():
            return _pending_notifications(db, user_id)
    except Exception:  # noqa: BLE001
        logger.warning("closure notifications unavailable (migrations 071/072 applied?)", exc_info=True)
        return []


def _pending_notifications(db, user_id):
    rows = db.execute(text("""
        SELECT id, notification_type, title, message, change_context, created_at
        FROM task_notifications
        WHERE recipient_id = :user AND closure_id IS NOT NULL AND dismissed_at IS NULL
        ORDER BY created_at, id
    """), {"user": user_id}).mappings().all()
    return [{"id": str(r["id"]), "notification_type": r["notification_type"], "title": r["title"], "message": r["message"],
             "change_context": r["change_context"] or {}, "created_at": r["created_at"].isoformat()} for r in rows]


# ---------------------------------------------------------------- the closer's own related logs
_ACTIVE_CLOSURE_OF_CALL = text("""
    SELECT k.id, k.client_id, k.closing_analysis_id, k.closing_user_id, k.closing_agent_name, k.closure_status,
           k.context_key, k.case_context, k.closed_at, a.fd_m_case_status_change AS status
    FROM call_analysis_results a
    JOIN fd_case_closures k ON k.closing_analysis_id = a.id AND k.reverted_at IS NULL
    WHERE a.call_id::text = :call
""")


def own_related_offer(db, call_id, user_id, *, owner_resolver=None):
    """What to offer the agent who just closed `call_id`: their own other open logs, same client and context.

    Empty (logs == []) when the call has no active closure of this agent's (no specific context, before the rollout
    date, reopened meanwhile) or nothing else matches, so the caller shows nothing. Read-only.
    """
    closure = db.execute(_ACTIVE_CLOSURE_OF_CALL, {"call": str(call_id)}).mappings().first()
    empty = {"closure_id": None, "logs": []}
    if not closure or str(closure["closing_user_id"]) != str(user_id) or not closure_status_of(closure["status"]):
        return empty
    logs = find_own_related(db, client_id=closure["client_id"], closing_analysis_id=closure["closing_analysis_id"],
                            user_id=user_id, key=closure["context_key"], owner_resolver=owner_resolver)
    if not logs:
        return empty
    client_name, client_number = _client_display(db, closure["client_id"])
    return {"closure_id": str(closure["id"]), "closure_status": closure["closure_status"],
            "closing_agent_name": closure["closing_agent_name"], "case_context": closure["case_context"],
            "client_id": closure["client_id"], "client_name": client_name, "client_number": client_number,
            "closed_at": closure["closed_at"].isoformat() if closure["closed_at"] else None, "logs": logs}


def confirm_own_related(db, closure_id, user_id, analysis_ids, *, owner_resolver=None):
    """The closer confirms: set the chosen own related logs to the closure's status, exactly as a Confirm does.

    Everything is re-derived under the per-client lock, so a log that was closed, reassigned, linked or changed
    context since the offer is skipped and reported, never touched. The status-save hook is NOT called (no second
    closure event, no new notifications: other agents were already notified when the closer saved). Each updated log is
    linked to the closure, so reopening the closing log restores it. Idempotent.

    Returns {"state": "confirmed"|"not_found"|"reverted", "updated": [...], "skipped": [...], "closure": {...}}.
    """
    empty = {"updated": [], "skipped": []}
    owner = db.execute(text("SELECT client_id, closing_user_id FROM fd_case_closures WHERE id = :i"), {"i": closure_id}).first()
    if not owner or str(owner[1]) != str(user_id):
        return {"state": "not_found", **empty}
    _lock_client(db, owner[0])
    # Re-read under the lock: a reopen that committed meanwhile must win.
    head = db.execute(text("""
        SELECT client_id, closing_analysis_id, context_key, closing_agent_name, closure_status, case_context, closed_at,
               reverted_at FROM fd_case_closures WHERE id = :i FOR UPDATE"""), {"i": closure_id}).mappings().first()
    if head["reverted_at"] is not None:
        return {"state": "reverted", **empty}
    status = head["closure_status"]
    eligible = {log["analysis_id"]: log for log in find_own_related(
        db, client_id=head["client_id"], closing_analysis_id=head["closing_analysis_id"], user_id=user_id,
        key=head["context_key"], owner_resolver=owner_resolver)}
    updated, skipped = [], []
    for analysis_id in dict.fromkeys(analysis_ids):         # a repeated id is processed once
        log = eligible.get(analysis_id)
        if not log:
            skipped.append({"analysis_id": analysis_id, "call_reference": None,
                            "reason": "No longer an open call log of yours for this client and context."})
            continue
        current = db.execute(text("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id = :a FOR UPDATE"),
                             {"a": analysis_id}).scalar()
        if closure_status_of(current):
            skipped.append({"analysis_id": analysis_id, "call_reference": log["call_reference"], "reason": f"Already {current}."})
            continue
        _set_case_status(db, analysis_id, log["call_id"], current, status, user_id)
        db.execute(text("""
            INSERT INTO fd_case_closure_links (id, closure_id, analysis_id, call_id, agent_user_id, confirmed_at, confirmed_by)
            VALUES (:id, :closure, :analysis, CAST(:call AS UUID), :agent, :now, :agent)
            ON CONFLICT DO NOTHING
        """), {"id": uuid.uuid4(), "closure": closure_id, "analysis": analysis_id, "call": log["call_id"],
               "agent": user_id, "now": _now()})
        date_entry = db.execute(text("SELECT fd_m_date_entry FROM call_analysis_results WHERE id = :a"), {"a": analysis_id}).scalar()
        updated.append({"analysis_id": analysis_id, "call_id": log["call_id"], "call_reference": log["call_reference"],
                        "previous_status": current, "new_status": status,
                        "fd_m_date_entry": date_entry.isoformat() if date_entry else None})
    client_name, client_number = _client_display(db, head["client_id"])
    return {"state": "confirmed", "updated": updated, "skipped": skipped, "closure_status": status,
            "closure": {"closure_id": str(closure_id), "closing_agent_name": head["closing_agent_name"],
                        "closure_status": status, "case_context": head["case_context"], "client_id": head["client_id"],
                        "client_name": client_name, "client_number": client_number,
                        "closed_at": head["closed_at"].isoformat() if head["closed_at"] else None}}


# ---------------------------------------------------------------- reversal
def revert_closure(db, closing_analysis_id, user):
    """The closer moved the record away from a closure status: reopen everything that depended on it."""
    closure = db.execute(text(
        "SELECT id, client_id FROM fd_case_closures WHERE closing_analysis_id = :a AND reverted_at IS NULL FOR UPDATE"),
        {"a": closing_analysis_id}).first()
    if not closure:
        return None
    _lock_client(db, closure[1])
    now = _now()
    closure_id = closure[0]
    db.execute(text("UPDATE fd_case_closures SET reverted_at = :n, reverted_by = :u WHERE id = :i"),
               {"n": now, "u": (user or {}).get("fd_user_id"), "i": closure_id})
    confirmed = db.execute(text("""
        UPDATE fd_case_closure_links SET reverted_at = :n WHERE closure_id = :i AND reverted_at IS NULL
        RETURNING agent_user_id, analysis_id, call_id::text
    """), {"n": now, "i": closure_id}).all()
    closed_as = db.execute(text("SELECT closure_status FROM fd_case_closures WHERE id = :i"), {"i": closure_id}).scalar()
    for _agent, analysis_id, call_id in confirmed:
        # Put back what confirmation replaced, but only if the agent has not changed the status since.
        current = db.execute(text("SELECT fd_m_case_status_change FROM call_analysis_results WHERE id = :a FOR UPDATE"),
                             {"a": analysis_id}).scalar()
        if closure_status_of(current) != closure_status_of(closed_as):      # either spelling of the same closure status
            continue
        previous = db.execute(text("""
            SELECT previous_status FROM fd_case_status_history
            WHERE analysis_id = :a AND lower(btrim(new_status)) = ANY(:spellings)
            ORDER BY id DESC LIMIT 1"""), {"a": analysis_id, "spellings": list(spellings_of(closure_status_of(closed_as)))}).scalar()
        _set_case_status(db, analysis_id, call_id, current, previous, (user or {}).get("fd_user_id"), set_date=bool(previous))
    # Notifications nobody acted on are withdrawn silently.
    db.execute(text("""
        UPDATE task_notifications SET dismissed_at = :n WHERE closure_id = :i AND notification_type = :t AND dismissed_at IS NULL
    """), {"n": now, "i": closure_id, "t": NOTIFICATION_CLOSED})
    info = db.execute(text("SELECT closing_agent_name, closure_status, case_context, client_id FROM fd_case_closures WHERE id = :i"),
                      {"i": closure_id}).first()
    client_name, client_number = _client_display(db, info[3])
    notified = 0
    for agent_id in {str(row[0]) for row in confirmed if row[0]}:
        ctx = {"closure_id": str(closure_id), "closing_agent_name": info[0], "closure_status": info[1],
               "case_context": info[2], "client_name": client_name, "client_number": client_number}
        message = (f"{info[0]} reopened the {info[2]} matter for client {client_number or info[3]} (it was {info[1]}). "
                   "Your matching call log(s) were set back to their previous status.")
        result = db.execute(text("""
            INSERT INTO task_notifications (id, recipient_id, title, message, notification_type, source_page, entity_id,
                                            change_context, created_at, closure_id)
            VALUES (:id, :r, 'Case Reopened by Another Agent', :m, :t, 'calls', :e, CAST(:c AS JSONB), :n, :closure)
            ON CONFLICT (closure_id, recipient_id, notification_type) WHERE closure_id IS NOT NULL DO NOTHING
        """), {"id": uuid.uuid4(), "r": agent_id, "m": message, "t": NOTIFICATION_REVERSED, "e": str(closure_id),
               "c": _json(ctx), "n": now, "closure": closure_id})
        notified += result.rowcount or 0
    return {"closure_id": str(closure_id), "reopened": len(confirmed), "notified": notified}


# ---------------------------------------------------------------- display + open-call eligibility
def inherited_closures(db, analysis_ids):
    """{analysis_id: info} for logs that currently inherit a closure (one query for the whole page).

    Fails safe: if the closure tables are missing (migrations 071/072 not applied yet) or the query
    fails, the Calls page still loads, just without closure information. A savepoint keeps the
    request's transaction usable after such an error.
    """
    ids = [i for i in dict.fromkeys(analysis_ids) if i is not None]
    if not ids:
        return {}
    try:
        with db.begin_nested():
            return _inherited_closures(db, ids)
    except Exception:  # noqa: BLE001
        logger.warning("closure lookup unavailable; rows shown without closure info", exc_info=True)
        return {}


def _inherited_closures(db, ids):
    rows = db.execute(text("""
        SELECT l.analysis_id, k.id AS closure_id, k.closing_agent_name, k.closure_status, k.case_context, k.client_id,
               k.closed_at, cl.client_name, cl.phone_number
        FROM fd_case_closure_links l
        JOIN fd_case_closures k ON k.id = l.closure_id AND k.reverted_at IS NULL
        LEFT JOIN clients cl ON cl.client_id = k.client_id
        WHERE l.analysis_id = ANY(:ids) AND l.reverted_at IS NULL
    """), {"ids": ids}).mappings().all()
    return {r["analysis_id"]: {
        "closure_id": str(r["closure_id"]), "closing_agent_name": r["closing_agent_name"], "closure_status": r["closure_status"],
        "case_context": r["case_context"], "client_id": r["client_id"], "client_name": r["client_name"] or "",
        "client_number": r["phone_number"] or "", "closed_at": r["closed_at"].isoformat() if r["closed_at"] else None,
    } for r in rows}
