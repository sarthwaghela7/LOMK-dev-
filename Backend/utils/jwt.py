import os
from datetime import datetime, timedelta, timezone

import jwt
from utils.config import SECRET_KEY

# python-jose was replaced with PyJWT (already an existing, unused
# dependency) to drop python-jose's transitive dependency on the `ecdsa`
# package. `ecdsa` carries an unpatched, "won't fix" timing-attack advisory
# (PYSEC-2026-1325 / the Minerva attack on P-256) with no upstream fix
# planned. This app only ever signs/verifies HS256 tokens (see
# JWT_ALGORITHM below), which is HMAC-SHA256 and never touches elliptic-curve
# code, so switching removes the flagged dependency entirely rather than
# living with an unreachable-but-still-flagged one.
ALGORITHM = os.getenv("JWT_ALGORITHM")

ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60))


def create_access_token(data: dict):
    to_encode = data.copy()

    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    if data.get("session_exp"):
        expire = min(expire, datetime.fromtimestamp(data["session_exp"], timezone.utc))
    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
