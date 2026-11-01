"""Opaque signed cursors bound to the complete filter set."""
import base64
import hashlib
import hmac
import json
from fastapi import HTTPException
from utils.config import SECRET_KEY


def scope_key(values):
    return hashlib.sha256(json.dumps(values, default=str, sort_keys=True).encode()).hexdigest()


def encode_cursor(day, identity, scope):
    data = json.dumps([1, str(day), str(identity), scope], separators=(",", ":")).encode()
    signature = hmac.new(SECRET_KEY.encode(), data, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature + data).decode().rstrip("=")


def decode_cursor(token, scope):
    try:
        if len(token) > 1024:
            raise ValueError()
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        signature, data = raw[:32], raw[32:]
        if not hmac.compare_digest(signature, hmac.new(SECRET_KEY.encode(), data, hashlib.sha256).digest()):
            raise ValueError()
        version, day, identity, saved_scope = json.loads(data)
        if version != 1 or saved_scope != scope:
            raise ValueError()
        from datetime import date
        return date.fromisoformat(day), identity
    except (ValueError, TypeError, KeyError):
        raise HTTPException(400, "Invalid cursor or changed filters") from None
