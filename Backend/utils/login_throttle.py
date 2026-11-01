"""Small in-process brute-force guard for the login endpoints.

Counts failed attempts per (client IP, account) and per account alone inside a
sliding window; once a limit is hit further attempts get HTTP 429 until the
window passes. State is per process, which still blunts online guessing on a
single replica; put a shared limiter in front if the API is scaled out.
"""
import threading
import time

from fastapi import HTTPException, Request

WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_IP_ACCOUNT = 5
MAX_FAILURES_PER_ACCOUNT = 20

_failures: dict[tuple, list[float]] = {}
_lock = threading.Lock()


def _keys(request: Request, account: str):
    ip = request.client.host if request.client else "unknown"
    account = (account or "").strip().lower()
    return ("ip", ip, account), ("acct", account)


def _recent(key, now):
    hits = [t for t in _failures.get(key, []) if now - t < WINDOW_SECONDS]
    if hits:
        _failures[key] = hits
    else:
        _failures.pop(key, None)
    return hits


def check_login_allowed(request: Request, account: str) -> None:
    now = time.monotonic()
    ip_key, acct_key = _keys(request, account)
    with _lock:
        blocked = (
            len(_recent(ip_key, now)) >= MAX_FAILURES_PER_IP_ACCOUNT
            or len(_recent(acct_key, now)) >= MAX_FAILURES_PER_ACCOUNT
        )
    if blocked:
        raise HTTPException(status_code=429, detail="Too many failed sign-in attempts. Try again later.")


def record_login_failure(request: Request, account: str) -> None:
    now = time.monotonic()
    with _lock:
        for key in _keys(request, account):
            _failures.setdefault(key, []).append(now)


def clear_login_failures(request: Request, account: str) -> None:
    ip_key, _ = _keys(request, account)
    with _lock:
        _failures.pop(ip_key, None)
