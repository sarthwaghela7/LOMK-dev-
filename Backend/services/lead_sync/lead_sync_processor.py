"""Idempotent Google Sheet -> fd_leads import.

Concurrency: one transaction holds ``pg_try_advisory_xact_lock`` (works through
the Supabase transaction pooler) for the whole run, so replicas never overlap;
the unique ``row_key`` is the second line of defence. Existing leads are never
updated: after creation every field is application-managed.
"""
import logging
from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from services.lead_sync import lead_mapper as mapper

logger = logging.getLogger("autoflint.lead_sync")
LOCK_KEY = 739412807


def _client_from_config():
    from services.lead_sync.sheets_client import SheetsClient
    from utils import config
    if not (config.GOOGLE_SERVICE_ACCOUNT_JSON and config.GOOGLE_SHEET_ID):
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID must both be set")
    return SheetsClient(config.GOOGLE_SERVICE_ACCOUNT_JSON, config.GOOGLE_SHEET_ID, config.LEAD_SHEET_SYNC_WORKSHEET)


def _cutoff(value):
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


def sync_leads(db, client=None, *, start_date="", now=None, notify=None):
    """Run one sync pass and return counters. Caller supplies the session."""
    from storage.models import FDLead, FDLeadSheetRow

    client = client or _client_from_config()
    cutoff = _cutoff(start_date)
    now = now or datetime.now(timezone.utc)
    new_leads = []
    stats = {"read": 0, "imported": 0, "already_imported": 0, "before_cutoff": 0, "errors": 0, "blank": 0}

    rows = client.fetch_rows()  # network I/O happens before the DB lock is taken
    if not rows:
        logger.info("[lead_sync] sheet is empty")
        return {**stats, "status": "ok"}
    header_at = next((i for i, r in enumerate(rows) if any(str(c).strip() for c in r)), None)
    fields, needs, missing = mapper.locate_columns(rows[header_at])
    if fields["date_received"] is None:
        raise RuntimeError("Sheet has no 'Entry date' column; refusing to import")
    if missing:
        logger.warning("[lead_sync] columns not found in sheet header (left blank): %s", ", ".join(missing))

    if not db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY}).scalar():
        logger.info("[lead_sync] another sync is running; skipping")
        return {**stats, "status": "locked"}

    seen = {r.row_key: r for r in db.query(FDLeadSheetRow).all()}
    for offset, row in enumerate(rows[header_at + 1:], start=header_at + 2):
        stats["read"] += 1
        key = None
        try:
            key = mapper.row_key(mapper.identity(row, fields)) if any(str(c).strip() for c in row) else None
            mapped = mapper.map_row(row, fields, needs)
            if mapped is None:
                stats["blank"] += 1
                continue
            _, record, entry = mapped
            if key in seen and seen[key].status == "imported":
                stats["already_imported"] += 1
                continue
            if cutoff and entry < cutoff:
                stats["before_cutoff"] += 1
                continue
            email = record.get("email", "")
            if email and ("@" not in email or " " in email):
                logger.warning("[lead_sync] row %s: email %r looks invalid; imported unchanged", offset, email)
            with db.begin_nested():
                number = db.execute(text("SELECT nextval('ld_lead_number_seq')")).scalar()
                record["lead_id"] = f"LD#{number}"
                lead = FDLead(id=uuid4(), lead_added_at=now, fd_m_leads=record)
                db.add(lead)
                ledger = seen.get(key)
                if ledger is None:
                    ledger = FDLeadSheetRow(id=uuid4(), row_key=key, first_seen_at=now)
                    db.add(ledger)
                    seen[key] = ledger
                ledger.sheet_row_number, ledger.lead_id = offset, lead.id
                ledger.status, ledger.error, ledger.last_seen_at = "imported", None, now
                db.flush()
            stats["imported"] += 1
            new_leads.append({
                "lead_id": record["lead_id"], "name": record.get("name", ""),
                "date_received_display": entry.strftime("%m/%d/%Y"),
                "client_type": record.get("client_type", ""), "source": record.get("source", ""),
                "needs": record.get("needs", ""),
            })
        except Exception as exc:
            stats["errors"] += 1
            message = f"{type(exc).__name__}: {exc}"[:500]
            logger.warning("[lead_sync] row %s skipped: %s", offset, message)
            if key:
                try:
                    with db.begin_nested():
                        ledger = seen.get(key)
                        if ledger is None or ledger not in db:
                            ledger = FDLeadSheetRow(id=uuid4(), row_key=key, first_seen_at=now)
                            db.add(ledger)
                            seen[key] = ledger
                        ledger.sheet_row_number, ledger.status, ledger.error, ledger.last_seen_at = offset, "error", message, now
                        db.flush()
                except Exception:
                    logger.exception("[lead_sync] could not record error for row %s", offset)
    db.commit()
    logger.info("[lead_sync] %s", stats)
    # Only after the import is durable; a notification failure never affects it.
    if new_leads:
        try:
            if notify is None:
                from services.lead_sync.notifier import notify_batched
                notify = notify_batched
            notify(new_leads)
        except Exception:
            logger.exception("[lead_sync] notification step failed")
    return {**stats, "status": "ok"}
