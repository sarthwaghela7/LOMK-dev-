import os
import re
from pathlib import Path

import pytest

# utils/config.py reads <repo root>/.env. A Git worktree has no .env of its own (it is gitignored), so the LLM settings the
# F3 tests construct their client from are missing there although the main checkout has them. Load ONLY these names, only when
# they are not already set, from FLINTDESK_TEST_ENV_FILE or the .env of the worktree's main checkout. Nothing else is read
# (never DATABASE_URL or any other setting) and nothing is printed or logged.
_TEST_LLM_ENV_NAMES = ("LLM_API_KEY", "LLM_URL", "LLM_MODEL")


def _main_checkout_env_file():
    root = Path(__file__).resolve().parents[1]
    git_pointer = root / ".git"
    if git_pointer.is_file():                                   # "gitdir: <main>/.git/worktrees/<name>"
        match = re.match(r"gitdir:\s*(.+?)/\.git/worktrees/", git_pointer.read_text())
        if match:
            return Path(match.group(1)) / ".env"
    return root / ".env"


def _load_test_llm_settings():
    missing = [name for name in _TEST_LLM_ENV_NAMES if not os.getenv(name)]
    if not missing:
        return
    path = Path(os.getenv("FLINTDESK_TEST_ENV_FILE") or _main_checkout_env_file())
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        name, separator, value = line.partition("=")
        name = name.strip()
        if separator and name in missing:
            os.environ.setdefault(name, value.strip().strip("\"'"))


_load_test_llm_settings()


@pytest.fixture(autouse=True)
def _clear_reference_cache():
    """routes.calls caches small reference tables (fd_sync_filters,
    fd_users) in-process for 60s (see _cached_reference) so real requests
    don't re-query them on every call. Tests run many times faster than
    that TTL and often mock these tables with different data per test, so
    without this a later test could silently see an earlier test's cached
    (mocked) result instead of its own.
    """
    from routes.calls import _reference_cache

    _reference_cache.clear()
    yield
    _reference_cache.clear()
