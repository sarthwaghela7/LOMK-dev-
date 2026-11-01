from types import SimpleNamespace

from routes.calls import _effective_extension


def call(extension, dst, key=None):
    return SimpleNamespace(extension=extension, dst=dst, audio_s3_key=key)


KEY = "vitel-call-recordings/2026/10/06/out-15174022144-201-20261006-133143-1791311503.130248.WAV"


def test_original_format_is_unchanged():
    assert _effective_extension(call("201", "15174022144", KEY)) == "201"
    assert _effective_extension(call("603", "603")) == "603"  # inbound, no audio


def test_new_format_uses_extension_from_recording_filename():
    assert _effective_extension(call("15174022144", "15174022144", KEY)) == "201"
    assert _effective_extension(call("+13463702322", "+13463702322",
        "x/out-+13463702322-204-20261006-131436-1.130204.WAV")) == "204"


def test_new_format_without_usable_recording_falls_back():
    assert _effective_extension(call("15174022144", "15174022144")) == "15174022144"
    # recording filed under the lead number itself, as the original format stored it
    assert _effective_extension(call("7373494072", "7373494072",
        "x/out-7373494072-2243374447-20261006-145559-1.130388.WAV")) == "2243374447"
    # a recording with a malformed source field is ignored
    assert _effective_extension(call("8474743356", "8474743356",
        "x/out-8474743356-abc-20261006-1-1.1.WAV")) == "8474743356"
    # inbound recordings never provide an agent extension
    assert _effective_extension(call("601", "601", "x/in-3128781255-+1555-20261006-1-1.1.WAV")) == "601"


def test_extension_from_audio_corrects_only_the_new_vitel_format():
    from services.Database_sync.f1 import F1Processor
    fix = F1Processor.extension_from_audio
    out201 = {"direction": "out", "src": "201"}
    assert fix(out201, call("+13463702322", "+13463702322")) == "201"
    assert fix({"direction": "out", "src": "2243374447"}, call("7373494072", "7373494072")) == "2243374447"
    assert fix(out201, call("201", "7373494072")) is None            # original format: leave alone
    assert fix({"direction": "in", "src": "+15551234567"}, call("601", "601")) is None
    assert fix({"direction": "out", "src": "6012360560x"}, call("5551234567", "5551234567")) is None
