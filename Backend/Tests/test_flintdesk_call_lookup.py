import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi import HTTPException

from routes.flintdesk.calls import _get_call_or_404


class DummyQuery:
    def __init__(self, result):
        self.result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.result


class DummyDB:
    def __init__(self, result):
        self.result = result

    def query(self, model):
        return DummyQuery(self.result)


def test_get_call_or_404_uses_uuid_for_valid_uuid_strings():
    call_id = uuid.uuid4()
    db = DummyDB(type("CallObj", (), {"call_id": call_id})())

    result = _get_call_or_404(str(call_id), db)

    assert result.call_id == call_id


def test_get_call_or_404_falls_back_to_call_reference_for_non_uuid_values():
    call = type("CallObj", (), {"call_id": uuid.uuid4(), "call_reference": "AF#147"})()
    db = DummyDB(call)

    result = _get_call_or_404("AF#147", db)

    assert result.call_reference == "AF#147"


def test_get_call_or_404_raises_404_for_unknown_reference():
    db = DummyDB(None)

    with pytest.raises(HTTPException) as exc:
        _get_call_or_404("AF#999999", db)

    assert exc.value.status_code == 404


def test_get_call_or_404_accepts_a_bare_af_number():
    # The Calls page's own "Call ID" export/display column shows just the
    # number (e.g. "8721"), not "AF#8721" -- accept it directly rather than
    # requiring the prefix to be retyped, since agents copy that value
    # straight back in for /calls/import.
    call = type("CallObj", (), {"call_id": uuid.uuid4(), "call_reference": "AF#8721"})()

    class RecordingQuery(DummyQuery):
        captured = None

        def filter(self, expression):
            RecordingQuery.captured = expression
            return self

    class RecordingDB(DummyDB):
        def query(self, model):
            return RecordingQuery(self.result)

    db = RecordingDB(call)
    result = _get_call_or_404("8721", db)

    assert result.call_reference == "AF#8721"
    assert RecordingQuery.captured.right.value == "AF#8721"
