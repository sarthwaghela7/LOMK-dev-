import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.Database_sync.f1 import F1Processor


def _make_pcm_wav_bytes(duration_seconds=1.5, sample_rate=8000, channels=1, bits_per_sample=16):
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = int(duration_seconds * byte_rate)
    riff_size = 36 + data_size
    header = b"RIFF" + struct.pack("<I", riff_size) + b"WAVE"
    fmt_chunk = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, channels, sample_rate, byte_rate, block_align, bits_per_sample)
    data_chunk = b"data" + struct.pack("<I", data_size)
    return header + fmt_chunk + data_chunk + (b"\x00" * data_size)


def _make_gsm_wav_bytes(duration_seconds=3.0):
    sample_rate = 8000
    channels = 1
    block_align = 33
    byte_rate = 1625
    data_size = int(duration_seconds * byte_rate)
    riff_size = 36 + data_size
    header = b"RIFF" + struct.pack("<I", riff_size) + b"WAVE"
    fmt_chunk = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 49, channels, sample_rate, byte_rate, block_align, 0)
    data_chunk = b"data" + struct.pack("<I", data_size)
    return header + fmt_chunk + data_chunk + (b"\x00" * data_size)


def _fake_s3_response(payload):
    class FakeBody:
        def read(self):
            return payload

    class FakeResp(dict):
        def __init__(self, payload):
            self["Body"] = FakeBody()

    return FakeResp(payload)


def test_get_wav_duration_seconds_handles_gsm_wav_header():
    proc = F1Processor.__new__(F1Processor)
    proc.bucket = "demo-bucket"
    proc.s3 = type("FakeS3", (), {
        "client": type("FakeClient", (), {
            "get_object": lambda self, Bucket, Key, Range=None: _fake_s3_response(_make_gsm_wav_bytes(3.0)),
            "head_object": lambda self, Bucket, Key: {"ContentLength": len(_make_gsm_wav_bytes(3.0))},
        })()
    })()

    duration = proc._get_wav_duration_seconds("demo.wav")
    assert abs(duration - 3.0) < 1e-6


def test_get_wav_duration_seconds_handles_pcm_wav_header():
    proc = F1Processor.__new__(F1Processor)
    proc.bucket = "demo-bucket"
    proc.s3 = type("FakeS3", (), {
        "client": type("FakeClient", (), {
            "get_object": lambda self, Bucket, Key, Range=None: _fake_s3_response(_make_pcm_wav_bytes(1.5)),
            "head_object": lambda self, Bucket, Key: {"ContentLength": len(_make_pcm_wav_bytes(1.5))},
        })()
    })()

    duration = proc._get_wav_duration_seconds("demo.wav")
    assert abs(duration - 1.5) < 1e-6


def test_partial_header_uses_declared_data_length_instead_of_object_size():
    # Extra trailing metadata must not turn an exactly-three-minute call into a long call.
    for seconds in (180, 180.25):
        payload = _make_pcm_wav_bytes(seconds)
        proc = F1Processor.__new__(F1Processor)
        proc.bucket = "demo-bucket"
        class Client:
            def get_object(self, **kwargs):
                return _fake_s3_response(payload[:4096])
            def head_object(self, **kwargs):
                raise AssertionError("Declared data size is available; do not estimate from object size")
        proc.s3 = type("S3", (), {"client": Client()})()
        assert proc._get_wav_duration_seconds("demo.wav") == seconds


def test_physical_call_identity_ignores_revised_vitel_billsec():
    first = type("Call", (), {
        "call_date": "2026-09-16", "call_time": "09:06:08",
        "src": "13128781255", "dst": "2039521565", "billsec": 49,
    })()
    revised = type("Call", (), {
        "call_date": "2026-09-16", "call_time": "09:06:08",
        "src": "13128781255", "dst": "2039521565", "billsec": 72,
    })()

    assert F1Processor._physical_call_identity(first) == F1Processor._physical_call_identity(revised)
