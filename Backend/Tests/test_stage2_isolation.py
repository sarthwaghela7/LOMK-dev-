from contextlib import contextmanager
from types import SimpleNamespace

from services.Database_sync.f3 import F3Processor


class FakeDb:
    def __init__(self):
        self.savepoints = 0
        self.rolled_back = 0

    @contextmanager
    def begin_nested(self):
        self.savepoints += 1
        try:
            yield
        except Exception:
            self.rolled_back += 1
            raise


def _processor(apply):
    processor = object.__new__(F3Processor)
    processor.application = "flintdesk"
    processor._apply_stage2 = apply
    return processor


def test_a_stage2_failure_is_contained_in_a_savepoint(capsys):
    def boom(record, response, db):
        raise RuntimeError("stage 2 exploded with secret client text")

    db = FakeDb()
    _processor(boom)._apply_stage2_isolated(SimpleNamespace(call_id="c1"), {}, db)   # must not raise
    assert db.savepoints == 1 and db.rolled_back == 1
    out = capsys.readouterr().out
    assert "Stage 2 not saved" in out and "Stage 1 saved" in out and "secret" not in out


def test_a_successful_stage2_save_runs_normally():
    calls = []
    db = FakeDb()
    _processor(lambda record, response, db_: calls.append(record.call_id))._apply_stage2_isolated(SimpleNamespace(call_id="c2"), {}, db)
    assert calls == ["c2"] and db.rolled_back == 0
