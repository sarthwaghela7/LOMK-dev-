"""Backfill hannah_ai_category for missed_call tasks created before this
categorization existed.

services/missed_call_sync.py only computes and persists a task's Front Desk
Agent/Website category at the moment that task is first created or
enriched -- and its own dedup guard (_already_covered) deliberately never
revisits a call once it is linked to a task (see that module's docstring).
Every missed_call task that already existed before this feature shipped
therefore stays permanently uncategorized without a one-time pass over the
historical data. This migration performs that pass.

Unlike services/missed_call_sync._physical_call_representatives (which
returns a category only for whichever CDR leg it picks as the
representative), this assigns the cluster's category to every leg in it --
a task can be linked to either leg of a physical call (see
sync_missed_calls_for_date's own "representative choice drift" note), so a
lookup keyed only by the chosen representative's call_id would miss tasks
linked to the other leg.

Idempotent: only ever touches rows where the category is still NULL, so
re-running (e.g. on every deploy) is a fast no-op once caught up.
"""
from __future__ import annotations

from services.hannah_ai_routing import category_for_dst_set, normalize_routing_number
from services.missed_call_sync import _PHYSICAL_LEG_TOLERANCE_SECONDS, _time_to_seconds, is_missed_call, missed_call_numbers
from storage.db import SessionLocal
from storage.models import Call, FDTask


def _categories_by_call_id_for_date(db, target_date, numbers) -> dict:
    day_calls = db.query(Call).filter(Call.call_date == target_date).all()
    candidates = [call for call in day_calls if is_missed_call(call, numbers)]
    if not candidates:
        return {}

    by_date_src: dict[tuple, list[Call]] = {}
    for call in candidates:
        key = (call.call_date, str(call.src or "").strip())
        by_date_src.setdefault(key, []).append(call)

    def _assign(cluster: list[Call], result: dict) -> None:
        category = category_for_dst_set({normalize_routing_number(c.dst) for c in cluster})
        if category:
            for c in cluster:
                result[c.call_id] = category

    result: dict = {}
    for group in by_date_src.values():
        group.sort(key=lambda c: _time_to_seconds(c.call_time))
        cluster: list[Call] = []
        for call in group:
            if cluster and _time_to_seconds(call.call_time) - _time_to_seconds(cluster[-1].call_time) > _PHYSICAL_LEG_TOLERANCE_SECONDS:
                _assign(cluster, result)
                cluster = []
            cluster.append(call)
        if cluster:
            _assign(cluster, result)
    return result


def migrate_backfill_hannah_ai_category() -> dict:
    db = SessionLocal()
    try:
        tasks = (
            db.query(FDTask)
            .filter(
                FDTask.task_type == "missed_call",
                FDTask.fd_m_tasks_call_id.isnot(None),
                FDTask.fd_m_tasks_hannah_ai_category.is_(None),
            )
            .all()
        )
        if not tasks:
            return {"updated": 0, "dates_scanned": 0}

        call_ids = {task.fd_m_tasks_call_id for task in tasks}
        linked_calls = {
            call.call_id: call
            for call in db.query(Call).filter(Call.call_id.in_(call_ids)).all()
        }
        dates = {call.call_date for call in linked_calls.values()}

        numbers = missed_call_numbers(db)
        if not numbers:
            return {"updated": 0, "dates_scanned": 0}

        categories_by_call_id: dict = {}
        for target_date in dates:
            categories_by_call_id.update(_categories_by_call_id_for_date(db, target_date, numbers))

        updated = 0
        for task in tasks:
            call = linked_calls.get(task.fd_m_tasks_call_id)
            if not call:
                continue
            category = categories_by_call_id.get(call.call_id)
            if category:
                task.fd_m_tasks_hannah_ai_category = category
                updated += 1
        db.commit()
        return {"updated": updated, "dates_scanned": len(dates)}
    finally:
        db.close()


if __name__ == "__main__":
    print(migrate_backfill_hannah_ai_category())
