"""WAV duration from header bytes already in memory (no network, no cost)."""
import struct


def wav_duration_seconds(data: bytes) -> float | None:
    """data_bytes / byte_rate from the RIFF header. Works for PCM and compressed
    WAVs (e.g. GSM 6.10) because byte_rate is the format's average rate.
    Returns None for anything that is not a parseable RIFF/WAVE."""
    try:
        if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            return None
        pos, byte_rate = 12, None
        while pos + 8 <= len(data):
            chunk_id = data[pos:pos + 4]
            size = struct.unpack_from("<I", data, pos + 4)[0]
            body = pos + 8
            if chunk_id == b"fmt " and body + 12 <= len(data):
                byte_rate = struct.unpack_from("<I", data, body + 8)[0]
            elif chunk_id == b"data":
                if not byte_rate:
                    return None
                # Streamed files may carry 0 / 0xFFFFFFFF; use what is present.
                if size in (0, 0xFFFFFFFF) or body + size > len(data):
                    size = len(data) - body
                return round(size / byte_rate, 3)
            pos = body + size + (size & 1)
    except Exception:  # noqa: BLE001 - called inline in the STT path; must never raise
        return None
    return None
