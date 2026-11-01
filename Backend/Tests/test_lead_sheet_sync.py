from datetime import date, datetime, timezone
from itertools import count

import pytest

from services.lead_sync import lead_mapper as m
from services.lead_sync import lead_sync_processor as proc
from services.lead_sync.sheets_client import SheetsClient, SheetsError

NEEDS = [
    "Will your needs involve any of the following: H1B (transfers/extensions)",
    "Will your needs involve any of the following: H4",
    "Will your needs involve any of the following: H4 EADs",
    "Will your needs involve any of the following: F1/STEM OPT",
    "Will your needs involve any of the following: Other",
]
HEADER = ["Entry date ", "Enter your email  address", "Enter your full name", "Enter your SMS",
          "Appointment type", "UTM source", "Give you detail requirement", *NEEDS]


def row(day="10/10/2026", email="a@x.com", name="Ann", phone="0771", needs=("", "", "", "", "")):
    return [day, email, name, phone, "Consultation", "google", "need help", *needs]


# ---- mapper -----------------------------------------------------------
def test_dates_are_parsed_day_first():
    assert m.parse_entry_date("11/10/2026") == date(2026, 10, 11)
    assert m.parse_entry_date("10/10/2026 14:03:00") == date(2026, 10, 10)
    with pytest.raises(m.RowError):
        m.parse_entry_date("31/02/2026")
    with pytest.raises(m.RowError):
        m.parse_entry_date("soon")


def test_columns_found_by_header_not_position():
    shuffled = list(reversed(HEADER))
    fields, needs, missing = m.locate_columns(shuffled)
    assert missing == []
    assert shuffled[fields["email"]].startswith("Enter your email")


def test_needs_only_lists_selected_options():
    cells = list(zip(["H1B (transfers/extensions)", "H4", "H4 EADs", "F1/STEM OPT", "Other"],
                     ["Yes", "No", "TRUE", "FALSE", "Need a visa for my cousin"]))
    assert m.build_needs(cells) == "H1B (transfers/extensions), H4 EADs, Other: Need a visa for my cousin"
    assert m.build_needs([("H4", ""), ("Other", "  ")]) == ""
    assert m.build_needs([("H4", "H4"), ("Other", "yes")]) == "H4, Other"


def test_map_row_blank_and_values():
    fields, needs, _ = m.locate_columns(HEADER)
    assert m.map_row([""] * len(HEADER), fields, needs) is None
    _, record, entry = m.map_row(row(day="11/10/2026", needs=("Yes", "", "", "", "")), fields, needs)
    assert record["date_received"] == "2026-10-11" and entry == date(2026, 10, 11)
    assert record["phone"] == "0771" and record["client_enquiry"] == "need help"
    assert record["needs"] == "H1B (transfers/extensions)"


def test_row_key_ignores_notes_but_not_identity():
    base = {"date_received": "10/10/2026", "email": "a@x.com", "name": "Ann", "phone": "1"}
    assert m.row_key(base) == m.row_key({**base, "client_enquiry": "edited"})
    assert m.row_key(base) != m.row_key({**base, "name": "Bob"})


# ---- sheets client ----------------------------------------------------
class _Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}

    def json(self):
        return self._body


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, *a, **k):
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(responses):
    c = SheetsClient("{}", "sheet", "Lead_Intake", sleep=lambda s: None)
    c._session = _Session(responses)
    return c


def test_client_retries_transient_errors_then_succeeds():
    c = _client([_Resp(429), TimeoutError(), _Resp(503), _Resp(200, {"values": [["h"]]})])
    assert c.fetch_rows() == [["h"]]


def test_client_gives_up_and_does_not_retry_permanent_errors():
    with pytest.raises(SheetsError):
        _client([_Resp(500)] * 4).fetch_rows()
    c = _client([_Resp(403), _Resp(200)])
    with pytest.raises(SheetsError):
        c.fetch_rows()
    assert len(c._session.responses) == 1


# ---- processor --------------------------------------------------------
class _Nested:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _Query:
    def __init__(self, items):
        self.items = items

    def all(self):
        return list(self.items)


class FakeDb:
    """Stands in for the Postgres session: the sequence and ledger live in memory."""
    def __init__(self, lock=True):
        self.ledger, self.leads, self.seq, self.lock, self.commits = [], [], count(1), lock, 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        return _Scalar(self.lock if "advisory" in sql else next(self.seq))

    def query(self, model):
        return _Query(self.ledger)

    def begin_nested(self):
        return _Nested()

    def add(self, obj):
        (self.ledger if hasattr(obj, "row_key") else self.leads).append(obj)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def __contains__(self, obj):
        return True


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def fetch_rows(self):
        return self.rows


NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def _persist(db, ledger_from):
    # a second run sees the ledger the first run wrote
    db.ledger = list(ledger_from)


def test_import_cutoff_ids_and_idempotency():
    rows = [HEADER,
            row(day="09/10/2026", name="Old"),
            row(day="10/10/2026", name="Ann"),
            row(day="10/10/2026", name="Bea", email="b@x.com"),   # same date, different lead
            row(day="12/10/2026", name="Cy", email="c@x.com", needs=("Yes", "", "", "", "")),
            row(day="99/10/2026", name="Bad", email="d@x.com"),  # malformed date
            [""] * len(HEADER)]
    db = FakeDb()
    stats = proc.sync_leads(db, FakeClient(rows), start_date="2026-10-10", now=NOW)
    assert (stats["imported"], stats["before_cutoff"], stats["errors"], stats["blank"]) == (3, 1, 1, 1)
    ids = [lead.fd_m_leads_lead_id for lead in db.leads]
    assert ids == ["LD#1", "LD#2", "LD#3"]
    assert [lead.fd_m_leads_date_received for lead in db.leads] == ["2026-10-10", "2026-10-10", "2026-10-12"]
    assert db.leads[2].fd_m_leads_needs == "H1B (transfers/extensions)"
    assert db.leads[0].lead_added_at == NOW and db.leads[0].fd_m_leads_owner_id is None
    assert any(r.status == "error" for r in db.ledger)

    # Re-running (or restarting the backend) with the same rows creates nothing new.
    again = proc.sync_leads(db, FakeClient(rows), start_date="2026-10-10", now=NOW)
    assert again["imported"] == 0 and again["already_imported"] == 3 and len(db.leads) == 3


def test_new_rows_are_picked_up_later():
    db = FakeDb()
    proc.sync_leads(db, FakeClient([HEADER, row()]), start_date="2026-10-10", now=NOW)
    stats = proc.sync_leads(db, FakeClient([HEADER, row(), row(name="New", email="n@x.com")]), start_date="2026-10-10", now=NOW)
    assert stats["imported"] == 1 and len(db.leads) == 2


def test_overlapping_run_is_skipped():
    db = FakeDb(lock=False)
    stats = proc.sync_leads(db, FakeClient([HEADER, row()]), start_date="", now=NOW)
    assert stats["status"] == "locked" and db.leads == []


def test_missing_entry_date_column_refuses_to_import():
    with pytest.raises(RuntimeError):
        proc.sync_leads(FakeDb(), FakeClient([["Name"], ["Ann"]]), now=NOW)


# ---- worksheet selection / request shape -------------------------------
class _RecordingSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return _Resp(200, {"values": [HEADER, row()]})


def test_request_targets_only_the_all_leads_tab_with_encoded_space():
    c = SheetsClient("{}", "SHEET123", "All Leads", sleep=lambda s: None)
    c._session = _RecordingSession()
    c.fetch_rows()
    (url, params, timeout), = c._session.calls
    assert url == "https://sheets.googleapis.com/v4/spreadsheets/SHEET123/values/%27All%20Leads%27"
    assert "Lead_Intake" not in url and timeout
    assert params["majorDimension"] == "ROWS"


def test_apostrophe_in_tab_name_is_escaped():
    c = SheetsClient("{}", "S", "Bob's Leads", sleep=lambda s: None)
    c._session = _RecordingSession()
    c.fetch_rows()
    assert c._session.calls[0][0].endswith("/values/%27Bob%27%27s%20Leads%27")


def test_scheduled_job_builds_client_from_configured_worksheet(monkeypatch):
    from utils import config
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setattr(config, "GOOGLE_SHEET_ID", "SHEET123")
    monkeypatch.setattr(config, "LEAD_SHEET_SYNC_WORKSHEET", "All Leads")
    client = proc._client_from_config()
    assert (client.sheet_id, client.worksheet) == ("SHEET123", "All Leads")


def test_default_worksheet_is_all_leads():
    import importlib, os
    from utils import config
    os.environ.pop("LEAD_SHEET_SYNC_WORKSHEET", None)
    assert importlib.reload(config).LEAD_SHEET_SYNC_WORKSHEET == "All Leads"


def test_sync_runs_end_to_end_on_rows_from_the_client_and_never_logs_ids(caplog):
    c = SheetsClient("{}", "SECRET-SHEET-ID", "All Leads", sleep=lambda s: None)
    c._session = _RecordingSession()
    db = FakeDb()
    with caplog.at_level("DEBUG"):
        stats = proc.sync_leads(db, c, start_date="2026-10-10", now=NOW)
    assert stats["imported"] == 1 and db.leads[0].fd_m_leads_lead_id == "LD#1"
    assert "SECRET-SHEET-ID" not in caplog.text


# ---- shapes seen in the real "All Leads" tab ----------------------------
REAL_HEADER = ["Entry Date", "Enter your Email Address", "Enter your Full Name", "Enter your Organization Name",
               "Enter your SMS", "Appointment Type",
               "Will your needs involve any of the following: H1B (transfers/extensions)\n===========\nWill your needs involve any of the following: Asylum (I-589)",
               "Will your needs involve any of the following: H4\n=======================\nWill your needs involve any of the following: T Visas",
               "Will your needs involve any of the following: H4 EADs\n=======================\nWill your needs involve any of the following: U Visas",
               "Will your needs involve any of the following: F1/STEM OPT\n======================\nWill your needs involve any of the following: VAWA applications",
               "Will your needs involve any of the following: Other",
               "Give your detailed requirements", "Previous Page URL", "UTM Source"]


def test_real_headers_map_with_nothing_missing():
    fields, needs, missing = m.locate_columns(REAL_HEADER)
    assert missing == []
    assert fields["client_enquiry"] == 11 and fields["source"] == 13
    assert [i for _, i in needs] == [6, 7, 8, 9, 10]


def test_real_row_with_label_cells_and_other_columns():
    fields, needs, _ = m.locate_columns(REAL_HEADER)
    cells = ["11/10/2026 05:35:41", "a@x.com", "Ann", "Org", "+1 555", "New Client",
             "Asylum (I-589)", "", "H4 EADs", "", "Other", "details", "url", "google"]
    _, record, entry = m.map_row(cells, fields, needs)
    assert entry == date(2026, 10, 11)  # 11 October, day first
    assert record["needs"] == "Asylum (I-589), H4 EADs, Other"
    assert record["source"] == "google" and record["client_enquiry"] == "details"


def test_older_text_month_dates_parse():
    assert m.parse_entry_date("June 3, 2026 08:49") == date(2026, 6, 3)
    assert m.parse_entry_date("August 14, 2026 17:05") == date(2026, 8, 14)
    with pytest.raises(m.RowError):
        m.parse_entry_date("Smarch 3, 2026")


# ---- Teams notification --------------------------------------------------
from services.lead_sync import notifier


def test_batch_notification_only_for_imported_leads_and_after_commit():
    sent = []
    db = FakeDb()
    rows = [HEADER, row(day="09/10/2026", name="Old"), row(name="Ann"), row(name="Bea", email="b@x.com")]
    proc.sync_leads(db, FakeClient(rows), start_date="2026-10-10", now=NOW, notify=lambda leads: sent.append((db.commits, leads)))
    (commits, leads), = sent  # one message for the batch, sent after db.commit()
    assert commits == 1 and [l["name"] for l in leads] == ["Ann", "Bea"]
    assert leads[0]["lead_id"] == "LD#1" and leads[0]["date_received_display"] == "10/10/2026"
    sent.clear()
    proc.sync_leads(db, FakeClient(rows), start_date="2026-10-10", now=NOW, notify=sent.append)
    assert sent == []  # nothing new, nothing sent


def test_notification_failure_does_not_break_the_import():
    db = FakeDb()
    def boom(leads):
        raise RuntimeError("teams down")
    stats = proc.sync_leads(db, FakeClient([HEADER, row()]), start_date="", now=NOW, notify=boom)
    assert stats["imported"] == 1 and db.commits == 1


def test_card_lists_ten_and_summarises_the_rest():
    leads = [{"lead_id": f"LD#{i}", "name": f"N{i}", "date_received_display": "10/10/2026", "needs": "H4"} for i in range(13)]
    card = notifier.build_card(leads)["attachments"][0]["content"]["body"]
    text = " ".join(b["text"] for b in card)
    assert "13 new leads" in text and "LD#9" in text and "LD#10" not in text and "and 3 more" in text


def test_webhook_post_retries_once_and_never_logs_the_url(caplog):
    url = "https://example.invalid/SECRET-WEBHOOK"
    calls = []
    def post(u, json=None, timeout=None):
        calls.append(u)
        return _Resp(503)
    with caplog.at_level("DEBUG"):
        ok = notifier.notify_new_leads([{"lead_id": "LD#1"}], url, post=post, sleep=lambda s: None)
    assert ok is False and len(calls) == 2 and "SECRET-WEBHOOK" not in caplog.text
    assert notifier.notify_new_leads([{"lead_id": "LD#1"}], "", post=post) is False
    assert notifier.notify_new_leads([{"lead_id": "LD#1"}], url, post=lambda *a, **k: _Resp(200), sleep=lambda s: None) is True


# ---- Teams batching: at most one message per interval, held leads sent together ---------------------------
from datetime import datetime as _dt, timedelta as _td, timezone as _tz


class _Clock:
    def __init__(self):
        self.now = _dt(2026, 10, 12, 9, 0, tzinfo=_tz.utc)

    def __call__(self):
        return self.now


def _lead(n):
    return {"lead_id": f"LD#{n}", "name": f"Lead {n}"}


def test_first_lead_after_a_quiet_period_is_sent_immediately():
    clock, sent = _Clock(), []
    b = notifier.LeadNotificationBatcher(_td(minutes=10), clock)
    b.add([_lead(1)])
    assert b.flush_if_due(lambda leads: sent.append(leads) or True) == 1
    assert [[l["lead_id"] for l in batch] for batch in sent] == [["LD#1"]]


def test_leads_inside_the_interval_wait_and_go_out_together():
    clock, sent = _Clock(), []
    send = lambda leads: sent.append([l["lead_id"] for l in leads]) or True
    b = notifier.LeadNotificationBatcher(_td(minutes=10), clock)
    b.add([_lead(1)]); b.flush_if_due(send)                       # 09:00 -> "LD#1"
    for minute, n in ((3, 2), (6, 3), (9, 4)):                     # one lead per 3 minutes, passes every 3
        clock.now += _td(minutes=3); b.add([_lead(n)])
        assert b.flush_if_due(send) == 0                           # still inside the 10 minutes: nothing posted
    assert b.pending == 3 and sent == [["LD#1"]]
    clock.now += _td(minutes=3)                                    # 09:12, interval over
    assert b.flush_if_due(send) == 3
    assert sent == [["LD#1"], ["LD#2", "LD#3", "LD#4"]] and b.pending == 0


def test_held_leads_are_sent_even_when_the_later_pass_finds_nothing_new():
    clock, sent = _Clock(), []
    send = lambda leads: sent.append(len(leads)) or True
    b = notifier.LeadNotificationBatcher(_td(minutes=10), clock)
    b.add([_lead(1)]); b.flush_if_due(send)
    clock.now += _td(minutes=2); b.add([_lead(2), _lead(3)]); b.flush_if_due(send)
    clock.now += _td(minutes=9); assert b.flush_if_due(send) == 2    # no new leads, but the window is over
    assert sent == [1, 2]


def test_a_failed_post_keeps_the_leads_for_the_next_pass_and_does_not_start_the_interval():
    clock = _Clock()
    b = notifier.LeadNotificationBatcher(_td(minutes=10), clock)
    b.add([_lead(1)])
    assert b.flush_if_due(lambda leads: False) == 0 and b.pending == 1
    clock.now += _td(minutes=1)
    assert b.flush_if_due(lambda leads: True) == 1                 # retried at once, not after another 10 minutes
    b.add([_lead(2)])
    assert b.flush_if_due(lambda leads: (_ for _ in ()).throw(RuntimeError("boom"))) == 0 and b.pending == 1


def test_a_large_batch_is_one_card_listing_ten_and_counting_the_rest():
    card = notifier.build_card([_lead(n) for n in range(1, 26)])["attachments"][0]["content"]["body"]
    assert card[0]["text"] == "25 new leads in FlintDesk" and "…and 15 more." in card[-2]["text"]


def test_no_webhook_means_nothing_is_queued_or_sent(monkeypatch):
    from utils import config
    monkeypatch.setattr(config, "LEAD_SHEET_TEAMS_WEBHOOK_URL", "")
    b = notifier.LeadNotificationBatcher()
    assert notifier.notify_batched([_lead(1)], batcher=b) == 0 and b.pending == 0


# ---- full needs header: every option column, combined cells included -----
_P = "Will your needs involve any of the following: "
FULL_HEADER = ["Entry Date", "Enter your Email Address", "Enter your Full Name", "Enter your SMS",
               "Appointment Type",
               f"{_P}H1B (transfers/extensions)\n===========\n{_P}Asylum (I-589)",
               f"{_P}H-4 / H-4 EAD\n=======\n{_P}H4\n=======\n{_P}T Visas",
               f"{_P}F1/STEM OPT\n=======\n{_P}H4 EADs\n=======\n{_P}U Visas",
               f"{_P}Green card / I-485\n=======\n{_P}F1/STEM OPT\n=======\n{_P}VAWA applications",
               f"{_P}Family-based", f"{_P}R - 1 religious worker", f"{_P}Employer compliance", f"{_P}Other",
               "Give your detailed requirements", "UTM Source"]


def test_every_needs_column_is_found_once():
    fields, needs, missing = m.locate_columns(FULL_HEADER)
    assert missing == [] and [i for _, i in needs] == [5, 6, 7, 8, 9, 10, 11, 12]
    cells = ["11/10/2026", "a@x.com", "Ann", "+1 555", "New", "Asylum (I-589)", "T Visas", "U Visas",
             "Green card / I-485", "Yes", "", "Yes", "Visa for cousin", "long notes", "metaads"]
    _, record, _ = m.map_row(cells, fields, needs)
    assert record["needs"] == ("Asylum (I-589), T Visas, U Visas, Green card / I-485, Family-based, "
                               "Employer compliance, Other: Visa for cousin")
    assert record["client_enquiry"] == "long notes" and record["source"] == "metaads" and record["name"] == "Ann"
