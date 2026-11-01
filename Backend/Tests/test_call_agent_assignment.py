from datetime import date
from types import SimpleNamespace

from routes.calls import _assignment_matches_call, _collapse_physical_calls


def test_assignment_matches_normalized_public_number_and_extension():
    call = SimpleNamespace(src="12243374447", dst="13125551212", extension="201")

    assert _assignment_matches_call(call, "2243374447", "201", {})


def test_assignment_requires_matching_lead_number_as_well_as_extension():
    call = SimpleNamespace(src="13128781255", dst="13125551212", extension="201")

    assert not _assignment_matches_call(call, "2243374447", "201", {})


def test_assignment_matches_configured_inbound_routing_destination():
    call = SimpleNamespace(src="13125551212", dst="603", extension="201", call_date=date(2026, 9, 15))

    assert _assignment_matches_call(call, "2243374447", "201", {"2243374447": {"603"}})


def test_call_list_collapses_revised_duration_for_the_same_cdr():
    earlier = SimpleNamespace(
        call_id="old", call_date=date(2026, 9, 15), call_time="10:00:00",
        src="13125551212", dst="12243374447", billsec=22, call_number=10,
        audio_s3_uri=None, audio_s3_key=None, processing_status="not processed",
    )
    mapped = SimpleNamespace(
        call_id="new", call_date=date(2026, 9, 15), call_time="10:00:00",
        src="13125551212", dst="12243374447", billsec=88, call_number=11,
        audio_s3_uri="s3://bucket/recording.wav", audio_s3_key="recording.wav",
        processing_status="processed",
    )

    assert _collapse_physical_calls([earlier, mapped]) == [mapped]
