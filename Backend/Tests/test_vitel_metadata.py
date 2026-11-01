"""Vitel metadata behaviour: recording filename, agent, disposition, routing.

All data is synthetic. Placeholders: CLIENT_* are fictional 555-01xx numbers,
LEAD is the public line, AGENT_EXTENSION / AI_ROUTE are generic. No customer
data belongs in this file.
"""
from datetime import date, time
from types import SimpleNamespace
from uuid import uuid4

import pytest

import routes.calls as calls_module
from routes.calls import (
    _hannah_ai_routing_leg_ids,
    _is_missed_call_number,
    assignment_agent_details,
)
from services import vitel_metadata as vm
from services.Database_sync import f1 as f1_module
from services.Database_sync.f1 import F1Processor
from services.missed_call_sync import _physical_call_representatives

LEAD = "15550100000"
CLIENT_A = "15550100001"
CLIENT_B = "15550100002"
AI_ROUTE = "2243860601"  # the AI attendant number (shared routing config, not a customer)
AGENT_EXTENSION = "102"
MISSED_NUMBERS = {"602", "604", AI_ROUTE}


def call(src, dst, disposition, *, at="10:00:00", billsec=30, extension=AGENT_EXTENSION,
         filename=None, call_date=date(2026, 1, 5)):
    hh, mm, ss = (int(x) for x in at.split(":"))
    return SimpleNamespace(
        call_id=uuid4(), call_date=call_date, call_time=time(hh, mm, ss), src=src, dst=dst,
        disposition=disposition, billsec=billsec, extension=extension, extname=extension,
        vitel_recording_filename=filename, audio_s3_key=None, audio_s3_uri=None,
    )


# --------------------------------------------------------------------------
# Agent resolution (one source of truth for inbound and outbound)
# --------------------------------------------------------------------------
class _Query:
    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, _name):  # options/filter/order_by ... all chain
        return lambda *a, **k: self

    def all(self):
        return self.rows


class _FakeDb:
    def __init__(self, assignments):
        self.assignments = assignments

    def query(self, *_):
        return _Query(self.assignments)


@pytest.fixture
def agent_env(monkeypatch):
    assignment = SimpleNamespace(
        number=LEAD, extension=AGENT_EXTENSION, agent_id=uuid4(), is_active=True,
        agent=SimpleNamespace(name="Agent One"),
        assignment_date=date(2025, 1, 1), created_at=date(2025, 1, 1),
    )
    monkeypatch.setattr(calls_module, "_system_numbers", lambda db: {LEAD[1:], "601", "603"})  # routes are system numbers
    monkeypatch.setattr(calls_module, "_extensions_by_parent_number", lambda db: {LEAD[1:]: {"601", "603"}})
    return _FakeDb([assignment])


def resolved_name(db, c):
    return assignment_agent_details(db, [c]).get(str(c.call_id), ("", "-"))[1]


def test_1_outbound_answered_uses_agent_mapping_and_vitel_filename(agent_env, monkeypatch):
    c = call(LEAD, CLIENT_A, "ANSWERED", filename="out-CLIENT-102-20260105-100000-1.1.WAV")
    assert resolved_name(agent_env, c) == "Agent One"
    assert vm.recording_owner_ids([c]) == {c.call_id}

    p = F1Processor.__new__(F1Processor)
    p.bucket = "b"
    monkeypatch.setattr(p, "_same_day_vitel_recordings", lambda dates: [c])
    monkeypatch.setattr(p, "_get_wav_duration_seconds", lambda key: 12.0)
    monkeypatch.setattr(p, "_match_audio", lambda *a: pytest.fail("legacy matcher must be bypassed"))
    s3 = {"key": "vitel-call-recordings/2026/01/05/" + c.vitel_recording_filename, "s3_uri": "s3://b/x"}
    mappings = p._map_audio([c], [s3])
    assert len(mappings) == 1 and mappings[0]["db_call"] is c
    assert mappings[0]["audio"]["s3_key"] == s3["key"]  # lands in the existing S3 pipeline fields


def test_2_inbound_answered_by_agent_resolves_agent_automatically(agent_env):
    c = call(CLIENT_A, "601", "ANSWERED", filename="in-LEAD-CLIENT-20260105-100000-1.1.WAV")
    assert calls_module._call_direction(c, calls_module._system_numbers(None)) == "Inbound"
    assert resolved_name(agent_env, c) == "Agent One"
    assert vm.inbound_agent_extension(c) == AGENT_EXTENSION


@pytest.mark.parametrize("extension", [AI_ROUTE, "603", "602", "15550100009", ""])
def test_inbound_non_agent_extension_is_never_an_agent(agent_env, extension):
    c = call(CLIENT_A, "601", "ANSWERED", extension=extension)
    assert vm.inbound_agent_extension(c) == ""
    assert resolved_name(agent_env, c) == "-"


def test_inbound_unanswered_leg_is_not_attributed_to_the_extension_that_rang(agent_env):
    assert resolved_name(agent_env, call(CLIENT_A, "601", "NO ANSWER")) == "-"


# --------------------------------------------------------------------------
# Calls page: failed ring-route legs; AI route; duplication
# --------------------------------------------------------------------------
@pytest.mark.parametrize("disposition", ["NO ANSWER", "BUSY"])
def test_3_4_failed_ring_route_leg_is_not_shown_on_calls(disposition):
    leg = call(CLIENT_A, "601", disposition)
    assert leg.call_id in _hannah_ai_routing_leg_ids([leg])


def test_answered_ring_route_leg_stays_a_normal_call():
    leg = call(CLIENT_A, "601", "ANSWERED")
    assert leg.call_id not in _hannah_ai_routing_leg_ids([leg])


def _fallback(ring_dst, ai_dst, src=CLIENT_A, ring_disposition="NO ANSWER", at="10:00:00", ai_at="10:00:20"):
    ring = call(src, ring_dst, ring_disposition, at=at, billsec=20)
    ai = call(src, ai_dst, "ANSWERED", at=ai_at, extension=AI_ROUTE, filename="in-LEAD-CLIENT-20260105-100020-1.1.WAV")
    companion = call(src, AI_ROUTE, "ANSWERED", at=ai_at, extension=AI_ROUTE)
    return ring, ai, companion


def _calls_page(calls):
    """Rows the Calls page keeps: analysis_read drops excluded legs and missed-call dsts."""
    excluded = _hannah_ai_routing_leg_ids(calls)
    return [c for c in calls if c.call_id not in excluded and not _is_missed_call_number(c, MISSED_NUMBERS)]


def _missed_calls_page(calls):
    candidates = [c for c in calls if _is_missed_call_number(c, MISSED_NUMBERS)]
    reps, _ = _physical_call_representatives(candidates)
    return reps


@pytest.mark.parametrize("ring,ai", [("601", "602"), ("603", "604")])
def test_5_to_8_and_13_no_answer_then_ai_is_one_missed_call_and_zero_calls(ring, ai):
    records = list(_fallback(ring, ai))
    assert _calls_page(records) == []
    missed = _missed_calls_page(records)
    assert len(missed) == 1


@pytest.mark.parametrize("disposition", ["NO ANSWER", "BUSY"])
def test_13_busy_or_no_answer_with_ai_answered_adds_zero_calls_one_missed(disposition):
    records = list(_fallback("601", "602", ring_disposition=disposition))
    assert len(_calls_page(records)) == 0
    assert len(_missed_calls_page(records)) == 1


def test_ai_record_is_not_a_calls_page_agent_call():
    ring, ai, companion = _fallback("603", "604")
    page = _calls_page([ring, ai, companion])
    assert ai not in page and companion not in page


def test_9_normal_inbound_answered_is_one_call_zero_missed(agent_env):
    c = call(CLIENT_A, "601", "ANSWERED", filename="in-LEAD-CLIENT-20260105-100000-1.1.WAV")
    assert _calls_page([c]) == [c]
    assert _missed_calls_page([c]) == []
    assert resolved_name(agent_env, c) == "Agent One"
    assert vm.recording_owner_ids([c]) == {c.call_id}


def test_14_reingesting_the_same_records_does_not_duplicate():
    p = F1Processor.__new__(F1Processor)
    ring, ai, companion = _fallback("601", "602")

    def as_cdr(c):
        return {"call_date": "2026-01-05", "call_time": c.call_time.strftime("%H:%M:%S"), "src": c.src,
                "dst": c.dst, "billsec": str(c.billsec), "disposition": c.disposition,
                "extension": c.extension, "callrecordfilename": c.vitel_recording_filename or ""}

    feed = [as_cdr(c) for c in (ring, ai, companion)]
    once = p._merge_call_legs(list(feed))
    twice = p._merge_call_legs(feed + feed)
    assert len(once) == len(twice) == 3  # duplicate CDRs collapse, nothing multiplies
    # And the Missed Calls collapse is stable when duplicate rows reach it.
    reps, _ = _physical_call_representatives([ai, companion, ai, companion])
    assert len(reps) == 1


# --------------------------------------------------------------------------
# Outbound regression
# --------------------------------------------------------------------------
def test_10_outbound_agent_resolution_and_extension_workaround_unchanged(agent_env):
    # Original format: extension is the agent extension.
    assert resolved_name(agent_env, call(LEAD, CLIENT_A, "ANSWERED")) == "Agent One"
    # Temporary format: extension == dst, agent recovered from the linked recording name.
    odd = call(LEAD, CLIENT_A, "ANSWERED", extension=CLIENT_A)
    odd.audio_s3_key = "vitel-call-recordings/2026/01/05/out-CLIENT-102-20260105-100000-1.1.WAV"
    assert calls_module._effective_extension(odd) == "102"
    assert resolved_name(agent_env, odd) == "Agent One"


def test_10_outbound_unanswered_attempt_keeps_its_own_recording():
    lone = call(LEAD, CLIENT_A, "NO ANSWER", filename="out-CLIENT-102-20260105-100000-1.1.WAV")
    assert vm.recording_owner_ids([lone]) == {lone.call_id}


# --------------------------------------------------------------------------
# Recording ownership (one file, many legs)
# --------------------------------------------------------------------------
def test_ai_recording_is_not_attached_to_the_failed_agent_leg():
    ring, ai, _ = _fallback("601", "602")
    ring.vitel_recording_filename = ai.vitel_recording_filename  # Vitel stamps every leg
    assert vm.recording_owner_ids([ring, ai]) == {ai.call_id}
    # Even if the AI hand-off record was not pulled, the failed ring leg must not own it.
    assert vm.recording_owner_ids([ring]) == set()


def test_answered_leg_owns_the_recording_over_a_longer_unanswered_leg():
    name = "in-LEAD-CLIENT-20260105-100000-1.1.WAV"
    answered = call(CLIENT_A, "601", "ANSWERED", billsec=5, filename=name)
    other = call(CLIENT_A, "605", "NO ANSWER", billsec=20, filename=name)
    assert vm.recording_owner_ids([other, answered]) == {answered.call_id}


# --------------------------------------------------------------------------
# F1 merge + insert
# --------------------------------------------------------------------------
def _cdr(time_, disposition, billsec, ext="101", name="in-LEAD-CLIENT-20260105-100000-1.1.WAV", dst="601"):
    return {"call_date": "2026-01-05", "call_time": time_, "src": CLIENT_A, "dst": dst,
            "billsec": str(billsec), "disposition": disposition, "extension": ext,
            "callrecordfilename": name}


def test_merge_prefers_answered_leg_even_when_unanswered_leg_is_longer():
    p = F1Processor.__new__(F1Processor)
    merged = p._merge_call_legs([_cdr("10:00:00", "NO ANSWER", 20, ext="102"), _cdr("10:00:00", "ANSWERED", 8, ext="101")])
    assert [(m["disposition"], m["extension"]) for m in merged] == [("ANSWERED", "101")]


def test_merge_prefers_voicemail_over_the_no_answer_ring_leg():
    p = F1Processor.__new__(F1Processor)
    merged = p._merge_call_legs([_cdr("10:00:00", "NO ANSWER", 25, dst="DIRECT"), _cdr("10:00:25", "VoiceMail", 22, dst="DIRECT")])
    assert len(merged) == 1 and merged[0]["disposition"] == "VoiceMail"


def test_merge_without_conversation_leg_keeps_original_largest_billsec_rule():
    p = F1Processor.__new__(F1Processor)
    merged = p._merge_call_legs([_cdr("10:00:00", "NO ANSWER", 3), _cdr("10:00:05", "BUSY", 9)])
    assert [m["billsec"] for m in merged] == ["9"]


def test_merge_keeps_filename_reported_only_by_a_dropped_leg():
    p = F1Processor.__new__(F1Processor)
    merged = p._merge_call_legs([_cdr("10:00:00", "ANSWERED", 30, name=""), _cdr("10:00:01", "NO ANSWER", 2, name="x.WAV")])
    assert merged[0]["callrecordfilename"] == "x.WAV" and merged[0]["disposition"] == "ANSWERED"


def test_insert_persists_vitel_filename(monkeypatch):
    captured = {}

    class FakeInsert:
        def values(self, rows):
            captured["rows"] = rows
            return self

        def on_conflict_do_nothing(self, **_):
            return self

        def returning(self, *_):
            return self

    class FakeDb:
        def execute(self, stmt, params=None):
            if isinstance(stmt, FakeInsert):
                return SimpleNamespace(fetchall=lambda: [(r["call_id"],) for r in captured["rows"]])
            return SimpleNamespace(scalar_one=lambda: 7)

        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr(f1_module, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(f1_module, "pg_insert", lambda _t: FakeInsert())
    monkeypatch.setattr(f1_module, "resolve_client_for_call", lambda *a: None)
    monkeypatch.setattr(f1_module, "reconcile_client_call_totals", lambda *a: None)
    p = F1Processor.__new__(F1Processor)
    monkeypatch.setattr(p, "_load_existing_call_keys", lambda calls: set())
    p._insert_calls([_cdr("10:00:00", "ANSWERED", 30, name="  /a/b/in-X.WAV "), _cdr("11:00:00", "ANSWERED", 30, name="")])
    assert [r["vitel_recording_filename"] for r in captured["rows"]] == ["in-X.WAV", None]


# --------------------------------------------------------------------------
# 12. Missing Vitel filename -> legacy fallback
# --------------------------------------------------------------------------
@pytest.mark.parametrize("missing", [None, "", "   "])
def test_12_missing_filename_falls_back_to_legacy_matcher(missing, monkeypatch):
    p = F1Processor.__new__(F1Processor)
    c = call(CLIENT_A, "601", "ANSWERED", filename=missing)
    sentinel = [{"db_call": c, "audio": {}}]
    seen = {}
    monkeypatch.setattr(p, "_same_day_vitel_recordings", lambda d: pytest.fail("no Vitel lookup without a filename"))
    monkeypatch.setattr(p, "_match_audio", lambda calls, audio: seen.update(calls=calls, audio=audio) or sentinel)
    assert p._map_audio([c], ["audio"]) == sentinel
    assert seen["calls"] == [c]


def test_vitel_named_file_is_not_reused_by_the_legacy_matcher(monkeypatch):
    p = F1Processor.__new__(F1Processor)
    p.bucket = "b"
    named = call(CLIENT_A, "601", "ANSWERED", filename="in-LEAD-CLIENT-20260105-100000-1.1.WAV")
    legacy = call(CLIENT_B, "601", "ANSWERED")
    files = [{"key": "p/" + named.vitel_recording_filename, "s3_uri": "s3://b/1"},
             {"key": "p/other.WAV", "s3_uri": "s3://b/2"}]
    monkeypatch.setattr(p, "_same_day_vitel_recordings", lambda d: [named])
    monkeypatch.setattr(p, "_get_wav_duration_seconds", lambda k: None)
    seen = {}
    monkeypatch.setattr(p, "_match_audio", lambda calls, audio: seen.update(calls=calls, audio=audio) or [])
    p._map_audio([named, legacy], files)
    assert seen["calls"] == [legacy]
    assert [a["key"] for a in seen["audio"]] == ["p/other.WAV"]


def test_vitel_file_not_yet_in_s3_waits_instead_of_guessing(monkeypatch):
    p = F1Processor.__new__(F1Processor)
    c = call(CLIENT_A, "601", "ANSWERED", filename="in-late-20260105-100000-1.1.WAV")
    monkeypatch.setattr(p, "_same_day_vitel_recordings", lambda d: [c])
    monkeypatch.setattr(p, "_match_audio", lambda *a: pytest.fail("legacy matcher must not guess"))
    assert p._map_audio([c], []) == []


# --------------------------------------------------------------------------
# 11. Voicemail
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["VOICEMAIL", "VoiceMail", "voice mail"])
def test_11_metadata_voicemail_overrides_the_ai_verdict(value):
    ai_said = {"fd_a_client_type": "New Client - Individual", "fd_a_call_outcome": "Callback requested",
               "fd_a_call_summary": "kept"}
    out = vm.apply_metadata_voicemail(ai_said, SimpleNamespace(disposition=value))
    assert out["fd_a_client_type"] == "Voicemail Call"
    assert "voicemail" in out["fd_a_call_outcome"].lower()  # what existing voicemail readers key on
    assert out["fd_a_call_summary"] == "kept"  # F3 analysis of the audio still happens


def test_11_non_voicemail_classification_is_untouched():
    ai_said = {"fd_a_client_type": "New Client - Individual", "fd_a_call_outcome": "x"}
    assert vm.apply_metadata_voicemail(ai_said, SimpleNamespace(disposition="ANSWERED")) is ai_said


def test_voicemail_with_audio_is_owner_of_its_recording_and_stays_in_the_pipeline():
    name = "external-LEAD-CLIENT-20260105-100000-1.1.WAV"
    v = call(CLIENT_A, "DIRECT", "VoiceMail", filename=name)
    ringing = call(CLIENT_A, "DIRECT", "NO ANSWER", filename=name)
    assert vm.recording_owner_ids([ringing, v]) == {v.call_id}  # F2 (STT) then F3 run on it


def test_inbound_ring_route_accepts_10_digit_line_extension(agent_env):
    # e.g. website line's own number used as an extension (no leading 1, src/dst have it)
    c = call(CLIENT_A, "603", "ANSWERED", extension=LEAD[1:])
    assert vm.inbound_agent_extension(c) == LEAD[1:]
    assert vm.inbound_agent_extension(call(CLIENT_A, "DIRECT", "ANSWERED", extension=LEAD[1:])) == ""
    assert vm.inbound_agent_extension(call(CLIENT_A, "603", "ANSWERED", extension=CLIENT_A[1:])) == ""


def test_answered_inbound_follows_outbound_edit_access_unanswered_stays_shared(agent_env):
    from services.call_access import is_inbound_call
    answered = call(CLIENT_A, "601", "ANSWERED")
    unanswered = call(CLIENT_A, "601", "NO ANSWER")
    assert is_inbound_call(answered, None) is False   # owned: assigned-agent rules apply
    assert is_inbound_call(unanswered, None) is True  # shared work


def test_disposition_helpers_are_case_and_spacing_insensitive():
    assert vm.is_unanswered_disposition("No Answer") and vm.is_unanswered_disposition("busy")
    assert vm.is_answered("answered") and not vm.is_answered("NO ANSWER")
    assert not vm.is_voicemail_disposition("ANSWERED")
    assert vm.clean_recording_filename(None) is None and vm.clean_recording_filename(" ") is None


def test_calls_page_filter_hides_unanswered_inbound_only():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy import and_
    from services.read_scope import is_inbound_call, unanswered_disposition
    from storage.models import Call
    sql = str(and_(is_inbound_call(Call), unanswered_disposition(Call)).compile(dialect=postgresql.dialect()))
    # inbound AND unanswered together; an outbound unanswered call fails the first term
    assert "calls.disposition" in sql and "NOT" in sql and " IN " in sql
    for value in ("NOANSWER", "BUSY", "FAILED"):
        assert value in str(unanswered_disposition(Call).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
