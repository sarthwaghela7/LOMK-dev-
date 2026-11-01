"""Teams notification for newly imported leads: batched, and at most one message per interval."""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger("autoflint.lead_sync")
MAX_LISTED = 10


def build_card(leads):
    """Adaptive Card payload accepted by a Teams Workflows webhook."""
    shown = leads[:MAX_LISTED]
    facts = []
    for lead in shown:
        parts = [lead.get("date_received_display"), lead.get("client_type"), lead.get("source")]
        line = " · ".join(p for p in parts if p)
        if lead.get("needs"):
            line += f" — Needs: {lead['needs']}"
        facts.append({"type": "TextBlock", "wrap": True, "spacing": "Small",
                      "text": f"**{lead.get('lead_id', '')}** {lead.get('name') or '(no name)'} — {line}"})
    if len(leads) > len(shown):
        facts.append({"type": "TextBlock", "text": f"…and {len(leads) - len(shown)} more.", "spacing": "Small"})
    title = f"{len(leads)} new lead{'s' if len(leads) != 1 else ''} in FlintDesk"
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard", "version": "1.4",
                "body": [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium"}, *facts,
                         {"type": "TextBlock", "text": "Assign an owner on the Leads page.", "isSubtle": True, "spacing": "Medium"}],
            },
        }],
    }


def notify_new_leads(leads, webhook_url, *, post=requests.post, sleep=time.sleep):
    """Post one message. Never raises; the webhook URL is never logged."""
    if not leads or not webhook_url:
        return False
    payload = build_card(leads)
    for attempt in (1, 2):
        try:
            response = post(webhook_url, json=payload, timeout=10)
            if response.status_code < 300:
                logger.info("[lead_sync] Teams notified about %s new lead(s)", len(leads))
                return True
            reason = f"HTTP {response.status_code}"
            if response.status_code not in (429, 500, 502, 503, 504):
                break
        except Exception as exc:  # network errors; message may embed the URL, so log the type only
            reason = type(exc).__name__
        if attempt == 1:
            sleep(2)
    logger.warning("[lead_sync] Teams notification failed (%s); %s lead(s) not announced", reason, len(leads))
    return False


class LeadNotificationBatcher:
    """Holds new leads and posts them to Teams together, never more often than `min_interval`.

    The first lead after a quiet period goes out on the next pass (no artificial delay). Leads that arrive
    while the interval is still running are kept and sent as ONE message when it is over (the card lists up to
    MAX_LISTED and says how many more). A failed post keeps the leads for the next pass. State is in memory:
    leads still waiting when the server restarts are not announced (they stay imported in FlintDesk).
    """

    def __init__(self, min_interval=timedelta(minutes=10), clock=lambda: datetime.now(timezone.utc)):
        self.min_interval = min_interval
        self.clock = clock
        self._pending = []
        self._last_sent = None
        self._lock = threading.Lock()

    def add(self, leads):
        with self._lock:
            self._pending.extend(leads or [])

    @property
    def pending(self):
        with self._lock:
            return len(self._pending)

    def flush_if_due(self, send):
        """Call `send(leads)` once if something is waiting and the interval has passed. Returns the count sent."""
        with self._lock:
            now = self.clock()
            if not self._pending:
                return 0
            if self._last_sent is not None and now - self._last_sent < self.min_interval:
                return 0
            batch = list(self._pending)
        ok = False
        try:
            ok = bool(send(batch))
        except Exception:  # never let a notification problem escape
            logger.exception("[lead_sync] Teams send raised")
        if not ok:
            return 0
        with self._lock:
            del self._pending[:len(batch)]
            self._last_sent = self.clock()
        return len(batch)


_BATCHER = None


def get_batcher():
    global _BATCHER
    if _BATCHER is None:
        from utils import config
        _BATCHER = LeadNotificationBatcher(timedelta(minutes=config.LEAD_SHEET_TEAMS_MIN_INTERVAL_MINUTES))
    return _BATCHER


def notify_batched(new_leads=None, webhook_url=None, batcher=None):
    """Queue `new_leads` (may be empty) and post the waiting leads if the interval allows."""
    from utils import config
    batcher = batcher or get_batcher()
    url = config.LEAD_SHEET_TEAMS_WEBHOOK_URL if webhook_url is None else webhook_url
    if not url:
        return 0
    batcher.add(new_leads)
    return batcher.flush_if_due(lambda leads: notify_new_leads(leads, url))
