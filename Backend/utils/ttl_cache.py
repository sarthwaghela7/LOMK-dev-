"""Tiny in-process TTL cache for expensive, frequently-repeated read endpoints.

Multiple users loading the same dashboard view at the same time (same date
range/filters) previously each triggered their own full DB query, which under
concurrent load competes for the small Supabase connection pool. Caching the
JSON-serializable result for a short window lets concurrent identical
requests share one query result instead of each re-querying the database.

Deliberately a plain get/set pair rather than a decorator, since callers need
to key on a subset of arguments (filters) while excluding per-request objects
like the DB session or current user.
"""

import inspect
import threading
import time
from functools import wraps

_DEFAULT_TTL_SECONDS = 20
_store: dict[tuple, tuple[float, object]] = {}
_lock = threading.Lock()
_flight_locks: dict[tuple, threading.Lock] = {}
_generation = 0


def invalidate_read_caches():
    global _generation
    with _lock:
        _generation += 1
        _store.clear()


def cache_get(namespace: str, key: tuple):
    with _lock:
        cached = _store.get((namespace, key))
        if cached and cached[0] > time.monotonic():
            return cached[1]
    return None


def cache_set(namespace: str, key: tuple, value: object, ttl_seconds: float = _DEFAULT_TTL_SECONDS):
    with _lock:
        _store[(namespace, key)] = (time.monotonic() + ttl_seconds, value)


def coalesced_read(namespace: str, ttl_seconds: float = 10, exclude: set[str] | None = None):
    """Cache a synchronous read and let identical concurrent callers share it.

    The lock is acquired before endpoint code touches SQLAlchemy, so duplicate
    browser loads wait without checking out an additional database connection.
    ``db`` and authentication objects are intentionally excluded from keys;
    callers must use this only for role-independent read endpoints.
    """
    excluded = set(exclude or {"db", "user"})

    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            key = tuple(
                (name, repr(value))
                for name, value in bound.arguments.items()
                if name not in excluded
            )
            # A read started before a committed write may finish afterwards.
            # Keep its result in the old generation, inaccessible to new reads.
            key = (_generation, *key)
            full_key = (namespace, key)
            cached = cache_get(namespace, key)
            if cached is not None:
                return cached
            with _lock:
                flight_lock = _flight_locks.setdefault(full_key, threading.Lock())
            with flight_lock:
                cached = cache_get(namespace, key)
                if cached is not None:
                    return cached
                result = function(*args, **kwargs)
                cache_set(namespace, key, result, ttl_seconds)
                return result

        return wrapped

    return decorate
