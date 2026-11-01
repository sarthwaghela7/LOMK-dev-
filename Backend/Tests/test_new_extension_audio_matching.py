import pytest
from types import SimpleNamespace
from datetime import date

from services.Database_sync.f1 import F1Processor


def test_outbound_audio_matches_any_configured_extension_source():
    processor = object.__new__(F1Processor)
    processor._configured_numbers = {"2243374447", "2243939098"}
    processor._configured_extension_parent_numbers = {"204": {"2243374447", "2243939098"}}
    processor._extension_parent_history = {}
    call = SimpleNamespace(src="12243374447", extension="204", call_date=None)

    assert processor._source_matches({"src": "204"}, call)


def test_extension_source_does_not_match_a_different_extension():
    processor = object.__new__(F1Processor)
    processor._configured_numbers = {"2243374447", "2243939098"}
    processor._configured_extension_parent_numbers = {"204": {"2243374447", "2243939098"}}
    processor._extension_parent_history = {}
    call = SimpleNamespace(src="12243374447", extension="204", call_date=None)

    assert not processor._source_matches({"src": "205"}, call)


def test_extension_move_keeps_audio_on_the_lead_number_valid_that_day():
    processor = object.__new__(F1Processor)
    processor._configured_numbers = {"2243374447", "2243939098"}
    processor._configured_extension_parent_numbers = {"204": {"2243939098"}}
    processor._extension_parent_history = {
        "204": [
            (date(2026, 9, 23), "2243374447"),
            (date(2026, 9, 24), "2243939098"),
        ]
    }

    old_call = SimpleNamespace(src="12243374447", extension="204", call_date=date(2026, 9, 23))
    new_call = SimpleNamespace(src="12243939098", extension="204", call_date=date(2026, 9, 24))

    assert processor._source_matches({"src": "204"}, old_call)
    assert processor._source_matches({"src": "204"}, new_call)
    assert not processor._source_matches({"src": "204"}, SimpleNamespace(
        src="12243374447", extension="204", call_date=date(2026, 9, 24)
    ))
