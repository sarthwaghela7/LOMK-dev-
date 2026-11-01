"""Read-only live tail of Vitel CDR metadata (Central time).

Usage (from AutoFlint_Backend):
    ./venv/bin/python Tests/tools/vitel_live_tail.py                 # today, all calls, poll every 10s
    ./venv/bin/python Tests/tools/vitel_live_tail.py --inbound       # only inbound (601-605 + DID)
    ./venv/bin/python Tests/tools/vitel_live_tail.py --date 2026-10-06 --no-follow
    ./venv/bin/python Tests/tools/vitel_live_tail.py --raw           # also print the raw XML record
"""
import argparse
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pytz
import requests
from utils.config import VITEL_BASE_URL, VITEL_LINE, VITEL_PASSWORD, VITEL_USERNAME
from services.Database_sync.f1 import F1Processor

CENTRAL = pytz.timezone("America/Chicago")
INBOUND_DST = re.compile(r"60\d|2243860601")
C = {"reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m", "green": "\033[32m",
     "red": "\033[31m", "yellow": "\033[33m", "cyan": "\033[36m", "magenta": "\033[35m"}
STATUS = {"ANSWERED": ("green", "● ANSWERED "), "NO ANSWER": ("yellow", "○ NO ANSWER"), "BUSY": ("red", "✖ BUSY     ")}


def fetch(day):
    params = {"username": VITEL_USERNAME, "password": VITEL_PASSWORD, "line": VITEL_LINE or "All",
              "startdate": day.isoformat(), "enddate": (day + timedelta(days=1)).isoformat()}
    r = requests.get(VITEL_BASE_URL, params=params, timeout=60)
    r.raise_for_status()
    raws = re.findall(r"<record>.*?</record>", r.text, re.S)
    return F1Processor()._parse_xml(r.text), raws


def pretty_phone(n):
    d = re.sub(r"\D", "", n or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else (n or "")


def render(x, raw=None):
    color, label = STATUS.get(x.get("disposition"), ("magenta", f"? {x.get('disposition')}"))
    inbound = bool(INBOUND_DST.fullmatch(x.get("dst") or ""))
    arrow = f"{C['cyan']}IN {C['reset']}" if inbound else f"{C['magenta']}OUT{C['reset']}"
    secs = int(x.get("billsec") or 0)
    line = (f"{C['dim']}{x['call_time']} CT{C['reset']}  {arrow}  {C['bold']}{pretty_phone(x.get('src')):<16}{C['reset']}"
            f" → {C['bold']}{pretty_phone(x.get('dst')):<16}{C['reset']}"
            f" {C[color]}{label}{C['reset']}  {secs // 60}:{secs % 60:02d}"
            f"  {C['dim']}ext={x.get('extension')} extname={x.get('extname')}{C['reset']}")
    return line + (f"\n    {C['dim']}{raw}{C['reset']}" if raw else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="Central date YYYY-MM-DD (default: today CT)")
    ap.add_argument("--interval", type=int, default=10)
    ap.add_argument("--inbound", action="store_true")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--no-follow", action="store_true", help="print once and exit")
    a = ap.parse_args()
    seen = Counter()  # identical records can repeat, so track occurrence counts
    first = True
    while True:
        day = datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else datetime.now(CENTRAL).date()
        try:
            calls, raws = fetch(day)
        except Exception as exc:
            print(f"{C['red']}fetch failed: {type(exc).__name__}{C['reset']}")
            calls, raws = [], []
        if first:
            print(f"{C['bold']}Vitel live CDR — {day} (Central) — {len(calls)} records so far{C['reset']}\n")
            first = False
        batch = Counter()
        new = []
        for x, raw in zip(calls, raws):
            key = (x["call_date"], x["call_time"], x.get("src"), x.get("dst"), x.get("disposition"), x.get("billsec"))
            batch[key] += 1
            if batch[key] > seen[key]:
                new.append((x, raw))
        seen = batch
        for x, raw in sorted(new, key=lambda p: p[0]["call_time"]):
            if a.inbound and not INBOUND_DST.fullmatch(x.get("dst") or ""):
                continue
            print(render(x, raw if a.raw else None))
        if a.no_follow:
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
