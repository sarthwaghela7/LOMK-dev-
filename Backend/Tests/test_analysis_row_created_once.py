from types import SimpleNamespace

from services.Database_sync import f3
from services.Database_sync.f3 import F3Processor


class AutoflushOffSession:
    """Like the app's session (autoflush=False): a lookup only sees rows that were flushed."""

    def __init__(self):
        self.flushed, self.pending = [], []

    def add(self, row):
        self.pending.append(row)

    def flush(self):
        self.flushed += self.pending
        self.pending = []

    def query(self, _model):
        return self

    def filter(self, *_):
        return self

    def first(self):
        return self.flushed[0] if self.flushed else None


def test_the_second_lookup_in_one_transaction_reuses_the_first_row(monkeypatch):
    # Stage 1 and Stage 2 each ask for the call's analysis row; the second must find the first, not create another.
    class FakeModel:
        call_id = None                      # the real model's column; only compared, never evaluated here

        def __init__(self, **kw):
            self.call_recording_date_normalized = None
            self.__dict__.update(kw)

    monkeypatch.setattr(f3, "CallAnalysisResult", FakeModel)
    processor = object.__new__(F3Processor)
    processor._normalize_call_date = lambda value: "2026-10-12"
    db = AutoflushOffSession()
    call = SimpleNamespace(call_id="c-1", call_number=7, call_reference="AF#7", call_date=None, billsec=60)
    first = processor._get_or_create_analysis_record(call, db)
    second = processor._get_or_create_analysis_record(call, db)
    assert first is second
    assert len(db.flushed) == 1
