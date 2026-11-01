"""Auto-create Missed Call tasks from real Call rows.

Once a call reaches dst = 602 (Front Desk Agent route) or dst = 604
(Website route), then dst = 2243860601, Hannah AI (the automated attendant)
answered instead of a human agent -- from a Front Desk Agent's perspective
that is a Missed Call. This module converts a newly ingested Call row into
a "missed_call" task on the Missed Calls page, becoming that page's data
source going forward in place of the old manual CSV import. It does not
touch the manual import path (routes/flintdesk/tasks.py POST /import) at
all -- previously imported rows are unaffected.

fd_sync_filters rows tagged target_page='missed_calls' (see migrations 048
and 053) are the single source of truth for which dst numbers count;
nothing here hardcodes "602"/"604"/"2243860601". Each qualifying task is
also labeled with its Hannah AI category (Front Desk Agent vs Website) --
see services/hannah_ai_routing.py for that classification.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from services.client_lifecycle import caller_phone_number, normalize_phone_number
from services.hannah_ai_routing import category_for_dst_set, normalize_routing_number as _hannah_normalize
from storage.models import Call, CallAnalysisResult, Client, FDSyncFilter, FDTask


def _normalize_routing_number(value: object) -> str:
    """Mirrors routes/calls.py's _normalize_routing_number exactly.

    CDR routing values (an extension-like number such as "602", a trunk
    number) are compared as-is. This is deliberately not
    services.client_lifecycle.normalize_phone_number, which requires at
    least 7 digits and would reject "602" outright -- that function answers
    "what is the caller's real phone number", this answers "does this
    routing value match a configured one".
    """
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def missed_call_numbers(db: Session) -> set[str]:
    """The dst numbers that route to Hannah AI (see migration 048)."""
    rows = (
        db.query(FDSyncFilter.value)
        .filter(FDSyncFilter.filter_type == "number", FDSyncFilter.target_page == "missed_calls")
        .all()
    )
    return {_normalize_routing_number(value) for (value,) in rows if value}


def is_missed_call(call: Call, numbers: set[str]) -> bool:
    """dst-only, deliberately: dst = 602/2243860601 means the call arrived
    at Hannah AI (inbound-only). Checking src too would also catch that
    number showing up as the source of some other call, which is not what
    "missed call" means here.
    """
    if not call or not numbers:
        return False
    return _normalize_routing_number(call.dst) in numbers


# The two CDR legs of one physical Hannah AI call (dst=602 and
# dst=2243860601) are usually logged at the exact same second, but have
# been observed up to a few seconds apart -- an exact call_time match is
# not reliable enough to treat them as the same physical call.
_PHYSICAL_LEG_TOLERANCE_SECONDS = 5


def _time_to_seconds(value) -> int:
    if not value:
        return 0
    return value.hour * 3600 + value.minute * 60 + value.second


def _physical_call_representatives(calls: list[Call]) -> tuple[list[Call], dict]:
    """Collapse the two CDR legs of each physical call into one representative.

    Groups by (call_date, src), then merges calls within
    _PHYSICAL_LEG_TOLERANCE_SECONDS of each other in time into the same
    cluster, and keeps whichever leg in a cluster actually has a matched
    audio recording (only one of the two legs can, since there is only one
    real recording per physical call) -- or arbitrarily the first if
    neither/both do.

    Also returns each representative's Hannah AI category (Front Desk Agent
    vs Website), derived from every dst value seen across its own cluster
    -- not just the chosen representative's own dst -- since which leg ends
    up representative depends on which one has matched audio, and either
    leg alone (602/604 vs 2243860601) is ambiguous on its own.
    """
    by_date_src: dict[tuple, list[Call]] = {}
    for call in calls:
        key = (call.call_date, str(call.src or "").strip())
        by_date_src.setdefault(key, []).append(call)

    representatives: list[Call] = []
    categories: dict = {}
    for group in by_date_src.values():
        group.sort(key=lambda c: _time_to_seconds(c.call_time))
        cluster: list[Call] = []
        for call in group:
            if cluster and _time_to_seconds(call.call_time) - _time_to_seconds(cluster[-1].call_time) > _PHYSICAL_LEG_TOLERANCE_SECONDS:
                representative = max(cluster, key=lambda c: bool(c.audio_s3_uri))
                representatives.append(representative)
                categories[representative.call_id] = category_for_dst_set({_hannah_normalize(c.dst) for c in cluster})
                cluster = []
            cluster.append(call)
        if cluster:
            representative = max(cluster, key=lambda c: bool(c.audio_s3_uri))
            representatives.append(representative)
            categories[representative.call_id] = category_for_dst_set({_hannah_normalize(c.dst) for c in cluster})
    return representatives, categories


def _resolved_client_name(client: Client | None, analysis: CallAnalysisResult | None) -> str | None:
    """Same source-of-truth precedence as the Missed Calls page's own
    display-time overlay (routes/flintdesk/tasks.py serialize()): the client
    profile name wins once someone has set it, otherwise fall back to the
    AI-extracted name from this call's own transcript -- a brand-new caller
    Hannah AI just took will almost always still have a blank profile.
    """
    if client and str(client.client_name or "").strip():
        return client.client_name
    if analysis and str(analysis.fd_a_client_name or "").strip():
        return analysis.fd_a_client_name
    return None


def sync_missed_calls_for_date(db: Session, target_date: date) -> dict:
    """Create or enrich one "missed_call" task per qualifying Call on target_date.

    Idempotent and safe to re-run:
      - A call that already has a linked task (fd_m_tasks_call_id, migration
        049) is skipped -- that link is the dedup key.
      - A call NOT yet linked is first matched, by client phone number +
        date, against an existing "missed_call" task that has no call link
        yet -- i.e. one a Team Lead already imported manually via the old
        CSV path before this call was fetched. When found, that existing row
        is enriched in place (linked to the real call, name refreshed) rather
        than creating a duplicate second row for the same missed call.
        fd_m_tasks_owner is never touched by this enrichment -- an owner a
        Team Lead already assigned must survive re-running this sync.
      - Only when neither of those applies is a brand-new task row created.
    """
    numbers = missed_call_numbers(db)
    if not numbers:
        return {"created": 0, "updated": 0, "skipped": 0, "scanned": 0}

    calls = db.query(Call).filter(Call.call_date == target_date).all()
    candidates = [call for call in calls if is_missed_call(call, numbers)]
    if not candidates:
        return {"created": 0, "updated": 0, "skipped": 0, "scanned": len(calls)}

    # A single physical Hannah AI call can be ingested as two separate Call
    # rows -- one CDR leg tagged dst=602, another tagged dst=2243860601 (see
    # services/Database_sync/f1.py's own leg-identity concept,
    # _physical_call_identity). Collapse them to one candidate per physical
    # call before creating tasks, or the same missed call would get two
    # duplicate rows on the Missed Calls page.
    candidates, categories_by_call_id = _physical_call_representatives(candidates)

    # A physical call's OTHER leg may already be linked to a task from an
    # earlier sync run -- e.g. that leg had its audio matched first, got
    # synced, and this run's within-batch dedup above now happens to prefer
    # this leg instead (its own audio has since been matched too). Checking
    # only this leg's own call_id would miss that and create a duplicate
    # second task for the same physical call, so instead check whether any
    # already-linked call shares this one's (date, src, ~time) identity,
    # regardless of which leg's call_id that task actually points at.
    already_linked_calls = (
        db.query(Call)
        .join(FDTask, FDTask.fd_m_tasks_call_id == Call.call_id)
        .filter(FDTask.task_type == "missed_call", Call.call_date == target_date)
        .all()
    )
    covered_seconds_by_src: dict[tuple, list[int]] = {}
    for call in already_linked_calls:
        key = (call.call_date, str(call.src or "").strip())
        covered_seconds_by_src.setdefault(key, []).append(_time_to_seconds(call.call_time))

    def _already_covered(call: Call) -> bool:
        key = (call.call_date, str(call.src or "").strip())
        call_seconds = _time_to_seconds(call.call_time)
        return any(
            abs(call_seconds - covered) <= _PHYSICAL_LEG_TOLERANCE_SECONDS
            for covered in covered_seconds_by_src.get(key, [])
        )

    already_linked_count = sum(1 for call in candidates if _already_covered(call))
    candidates = [call for call in candidates if not _already_covered(call)]
    if not candidates:
        return {"created": 0, "updated": 0, "skipped": already_linked_count, "scanned": len(calls)}

    client_ids = {call.client_id for call in candidates if call.client_id}
    clients_by_id = (
        {client.client_id: client for client in db.query(Client).filter(Client.client_id.in_(client_ids)).all()}
        if client_ids
        else {}
    )
    analysis_by_call_id = {
        str(a.call_id): a
        for a in db.query(CallAnalysisResult)
        .filter(CallAnalysisResult.call_id.in_([str(call.call_id) for call in candidates]))
        .all()
    }

    # Existing manually-imported missed_call tasks with no call link yet --
    # batch-loaded once and keyed by normalized phone digits, so a
    # newly-fetched call can be matched to (and enrich) the row a Team Lead
    # already imported instead of creating a duplicate for the same missed
    # call.
    unlinked_by_phone: dict[str, list[FDTask]] = {}
    for task in db.query(FDTask).filter(
        FDTask.task_type == "missed_call",
        FDTask.fd_m_tasks_call_id.is_(None),
    ).all():
        phone = normalize_phone_number(task.fd_m_tasks_contact_number)
        if phone:
            unlinked_by_phone.setdefault(phone, []).append(task)

    created = 0
    updated = 0
    now = datetime.now(timezone.utc)
    for call in candidates:
        client = clients_by_id.get(call.client_id) if call.client_id else None
        analysis = analysis_by_call_id.get(str(call.call_id))
        # Same client_id/phone resolution already used everywhere else in
        # FlintDesk -- see storage/models.py Client and
        # services/client_lifecycle.py.
        contact_number = client.phone_number if client else caller_phone_number(db, call.src, call.dst)
        client_name = _resolved_client_name(client, analysis)
        phone_digits = normalize_phone_number(contact_number)
        category = categories_by_call_id.get(call.call_id)

        existing_task = None
        for task in unlinked_by_phone.get(phone_digits, []):
            task_date = task.fd_m_tasks_missed_call_date or task.fd_m_tasks_miscall_added_date
            if task_date == call.call_date:
                existing_task = task
                break

        if existing_task:
            existing_task.fd_m_tasks_call_id = call.call_id
            if not existing_task.fd_m_tasks_missed_call_date:
                existing_task.fd_m_tasks_missed_call_date = call.call_date
            if client_name:
                existing_task.fd_m_tasks_name = client_name
            existing_task.fd_m_tasks_hannah_ai_category = category
            existing_task.updated_at = now
            # A second candidate call for this same phone+date must not also
            # claim the row just enriched.
            unlinked_by_phone[phone_digits].remove(existing_task)
            updated += 1
            continue

        db.add(
            FDTask(
                id=uuid4(),
                task_type="missed_call",
                fd_m_tasks_missed_call_date=call.call_date,
                fd_m_tasks_name=client_name,
                fd_m_tasks_contact_number=contact_number,
                fd_m_tasks_hannah_ai_category=category,
                fd_m_tasks_call_id=call.call_id,
                created_at=now,
                updated_at=now,
            )
        )
        created += 1
    db.commit()
    return {"created": created, "updated": updated, "skipped": already_linked_count, "scanned": len(calls)}
