"""Guarded 15-user workflow test. Credentials/payloads stay in a private JSON file."""
import argparse
import concurrent.futures
import json
import statistics
import threading
import time
from datetime import date, timedelta
from urllib.parse import urlparse

import requests


class Results:
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = []
    def add(self, step, elapsed_ms, status):
        with self.lock:
            self.rows.append((step, elapsed_ms, status))


def run_user(base, user, start, end, barrier, results):
    session = requests.Session()
    def request(step, method, path, **kwargs):
        started = time.perf_counter()
        response = session.request(method, base + path, timeout=45, **kwargs)
        results.add(step, (time.perf_counter() - started) * 1000, response.status_code)
        response.raise_for_status()
        return response.json() if response.content else None

    barrier.wait()
    login = request("login", "POST", "/fd-auth/login",
                    json={"username": user["username"], "password": user["password"]})
    token = login.get("access_token") or login.get("token")
    if not token:
        raise RuntimeError("Login response did not contain a token")
    session.headers["Authorization"] = f"Bearer {token}"
    dates = f"start_date={start}&end_date={end}"
    request("dashboard", "GET", f"/api/dashboard/summary?{dates}")
    request("calls_month", "GET", f"/calls/analysis?{dates}&target_page=calls&page=1&page_size=100&cursor_mode=true")
    request("call_id_search", "GET", f"/calls/analysis?{dates}&target_page=calls&page=1&page_size=100&call_id={user['call_id']}")
    request("leads", "GET", f"/api/flintdesk/leads?{dates}&page=1&page_size=100")
    request("missed_calls", "GET", f"/api/flintdesk/tasks?{dates}&task_type=missed_call")
    request("save_field", "PATCH", f"/api/flintdesk/calls/{user['call_id']}/field",
            json=user["save"])
    request("small_import", "POST", "/api/flintdesk/calls/import",
            json={"records": user["import_records"]})


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def validate_users(users):
    if len(users) != 15:
        raise ValueError("Exactly 15 isolated test users are required")
    if len({user["call_id"] for user in users}) != 15:
        raise ValueError("Each user must have a unique test call_id to prevent write conflicts")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--users-file", required=True)
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument("--allow-production-writes", action="store_true")
    parser.add_argument("--start-date", default=str(date.today() - timedelta(days=30)))
    parser.add_argument("--end-date", default=str(date.today()))
    args = parser.parse_args()
    if not args.allow_writes:
        raise SystemExit("Refusing: workflow saves/imports data; pass --allow-writes on an approved test environment")
    host = (urlparse(args.base_url).hostname or "").lower()
    local = host in {"localhost", "127.0.0.1"} or host.endswith(".internal")
    if not local and not args.allow_production_writes:
        raise SystemExit("Refusing non-local writes without --allow-production-writes")
    with open(args.users_file, encoding="utf-8") as handle:
        users = json.load(handle)["users"]
    try:
        validate_users(users)
    except (KeyError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    barrier = threading.Barrier(15)
    results = Results()
    errors = []
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(run_user, args.base_url.rstrip("/"), user,
                                   args.start_date, args.end_date, barrier, results)
                   for user in users]
        for future in futures:
            try: future.result()
            except Exception as exc: errors.append(type(exc).__name__)
    grouped = {}
    for step, elapsed, status in results.rows:
        grouped.setdefault(step, []).append((elapsed, status))
    report = {step: {"requests": len(rows),
                     "errors": sum(status >= 400 for _, status in rows),
                     "p50_ms": round(percentile([value for value, _ in rows], .50), 1),
                     "p95_ms": round(percentile([value for value, _ in rows], .95), 1),
                     "max_ms": round(max(value for value, _ in rows), 1)}
              for step, rows in grouped.items()}
    print(json.dumps({"users": 15, "wall_seconds": round(time.perf_counter() - started, 2),
                      "workflow_errors": len(errors), "steps": report}, indent=2))
    raise SystemExit(1 if errors or any(row["errors"] for row in report.values()) else 0)


if __name__ == "__main__":
    main()
