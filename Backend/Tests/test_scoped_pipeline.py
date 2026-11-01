import pytest
from datetime import date
from types import SimpleNamespace

from services.Database_sync.f1 import F1Processor
from services.Database_sync.f2 import F2Processor
from services.Database_sync.f3 import F3Processor


def test_f1_phone_scope_keeps_only_requested_configured_line():
    processor = F1Processor.__new__(F1Processor)
    processor._load_sync_filters = lambda: ({}, {"6303353022", "2243374447"})
    calls = [
        {"src": "+16303353022", "dst": "15551234567", "extension": ""},
        {"src": "2243374447", "dst": "15557654321", "extension": ""},
    ]

    assert processor._filter_calls(calls, phone_number="6303353022") == [calls[0]]


def test_f1_phone_scope_rejects_an_unconfigured_line():
    processor = F1Processor.__new__(F1Processor)
    processor._load_sync_filters = lambda: ({}, {"2243374447"})

    try:
        processor._filter_calls([], phone_number="6303353022")
    except ValueError as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("Expected an unconfigured line to be rejected")


def test_f1_parses_vitel_xml_with_a_bare_ampersand_in_text():
    processor = F1Processor.__new__(F1Processor)
    calls = processor._parse_xml(
        "<response><record><calldate>2026-08-20 10:00:00</calldate>"
        "<src>6303353022</src><dst>A & B</dst></record></response>"
    )

    assert calls == [{"call_date": "2026-08-20", "call_time": "10:00:00", "src": "6303353022", "dst": "A & B"}]


def test_f3_phone_normalization_accepts_country_code():
    assert F3Processor._normalize_phone_number("+1 (630) 335-3022") == "6303353022"


def test_f1_audio_destination_matching_normalizes_phone_formats():
    processor = F1Processor.__new__(F1Processor)
    assert processor._dst_matches(
        {"dst": "+1 630 335 3022", "direction": "out"},
        type("Call", (), {"dst": "6303353022"})(),
    )


def test_f1_hannah_ai_audio_matches_direct_source_before_extension_fallback():
    processor = F1Processor.__new__(F1Processor)
    # The source is also present in extension on these CDR rows. A direct
    # normalized match must win instead of treating it as a trunk extension.
    call = SimpleNamespace(src="+14155550123", extension="+14155550123", call_date=None)
    assert processor._source_matches({"src": "14155550123"}, call)


def test_f1_website_hannah_handoff_matches_its_underlying_trunk():
    processor = F1Processor.__new__(F1Processor)
    assert processor._dst_matches(
        {"dst": "+1 603", "direction": "in"},
        type("Call", (), {"dst": "604"})(),
    )


def test_f1_rematches_existing_calls_without_audio_when_no_new_calls_arrive():
    processor = F1Processor.__new__(F1Processor)
    missing_call = object()
    mapping = object()
    recorded = {}

    processor._fetch_calls_for_date = lambda _: []
    processor._filter_calls = lambda calls, phone_number=None: calls
    processor._merge_call_legs = lambda calls: calls
    processor._insert_calls = lambda calls: ([], 3)
    processor._get_latest_batch = lambda _: ["audio"]
    processor._missing_audio_calls_for_date = lambda target_date, phone_number: [missing_call]
    processor._match_audio = lambda calls, audio: [mapping]
    processor._update_audio_paths = lambda mappings: recorded.setdefault("mappings", mappings)
    processor._log_sync = lambda *args: recorded.setdefault("sync", args)

    processor.run(target_date=date(2026, 9, 10))

    assert recorded["mappings"] == [mapping]
    assert recorded["sync"][1] == 0


def test_f3_cutoff_includes_requested_backfill_date():
    assert date(2026, 8, 20) >= F3Processor.F3_START_DATE


def test_corrupt_audio_is_not_retryable_forever():
    assert F2Processor._is_permanent_audio_failure(
        "Audio file is corrupted/empty (60 bytes)"
    )
    assert not F2Processor._is_permanent_audio_failure("STT provider timed out")


def test_f1_extension_201_audio_matches_only_its_line_and_extension():
    processor = F1Processor.__new__(F1Processor)
    processor._configured_numbers = {"3128781255", "2243374447"}
    processor._configured_extension_parent_numbers = {"201": {"2243374447"}}
    processor._extension_parent_history = {}
    audio = {"src": "201", "direction": "out"}
    assert processor._source_matches(audio, SimpleNamespace(src="+12243374447", extension="201", call_date=None))
    assert not processor._source_matches(audio, SimpleNamespace(src="13128781255", extension="201", call_date=None))
    assert not processor._source_matches(audio, SimpleNamespace(src="12243374447", extension="202", call_date=None))
