"""Command line entry: ``python -m monitoring --help``."""
import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from . import cumulative
from .config import load_settings
from .export import append_to_workbook, write_csv
from .report import COLUMNS, build_report, persist


def _parse_day(raw):
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _parser():
    p = argparse.ArgumentParser(prog="python -m monitoring", description="Daily usage & cost report")
    p.add_argument("--date", type=_parse_day, help="reporting day YYYY-MM-DD (default: yesterday in MONITOR_REPORT_TZ)")
    p.add_argument("--to", type=_parse_day, help="end of an inclusive range starting at --date")
    p.add_argument("--csv", type=Path, help="append the report to this CSV (created if missing)")
    p.add_argument("--xlsx-template", type=Path, help="existing Daily_AI_Usage_and_Cost_Tracker.xlsx")
    p.add_argument("--xlsx-out", type=Path, help="where to write the updated workbook (default: next to the template, *_updated.xlsx)")
    p.add_argument("--in-place", action="store_true", help="modify the template itself (a backup is made first)")
    p.add_argument("--replace-existing", action="store_true", help="update a date that already exists (never clears filled cells)")
    p.add_argument("--mtd", action="append", default=[], metavar="PROVIDER=USD",
                   help="month-to-date cost from a dashboard (railway|supabase) through --date; stored as a snapshot, "
                        "reported as a daily difference, never as a daily value")
    p.add_argument("--billing-cycle-day", action="append", default=[], metavar="PROVIDER=DAY",
                   help="billing period start day for railway/supabase (default 1)")
    p.add_argument("--dry-run", action="store_true", help="print only; do not write the database, CSV or workbook")
    p.add_argument("--check-providers", action="store_true", help="probe each provider's access (read-only) and exit")
    return p


def _kv(items, cast):
    out = {}
    for item in items:
        k, _, v = item.partition("=")
        out[k.strip().lower()] = cast(v)
    return out


def _clients(settings):
    import requests
    cloudwatch = cost_explorer = None
    try:
        import boto3
        cloudwatch = boto3.client("cloudwatch", region_name=settings.aws_region)
        cost_explorer = boto3.client("ce", region_name="us-east-1") if settings.aws_cost_explorer_enabled else None
    except Exception as exc:  # noqa: BLE001
        print(f"[monitoring] AWS clients unavailable: {type(exc).__name__}", file=sys.stderr)
    return requests, cloudwatch, cost_explorer


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    settings = load_settings()
    http, cloudwatch, cost_explorer = _clients(settings)

    if args.check_providers:
        from .check import run_checks
        return run_checks(settings, http, cloudwatch, cost_explorer)

    today = datetime.now(settings.tz).date()
    first = args.date or today - timedelta(days=1)
    last = args.to or first
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    cycle_days = _kv(args.billing_cycle_day, int)
    mtd = _kv(args.mtd, float)

    from storage.db import engine
    reports = []
    with engine.connect() as app_conn:
        if app_conn.dialect.name == "postgresql":
            app_conn.execute(text("SET TRANSACTION READ ONLY"))  # app tables are never written
        for day in days:
            with engine.begin() as store_conn:  # monitoring tables only
                for provider, usd in mtd.items():
                    if provider not in ("railway", "supabase"):
                        raise SystemExit(f"--mtd supports railway|supabase, not {provider!r}")
                    if len(days) > 1:
                        raise SystemExit("--mtd applies to a single --date")
                    if not args.dry_run:
                        cumulative.save_snapshot(store_conn, provider, "cost",
                                                 cumulative.period_start_for(day, cycle_days.get(provider, 1)), day, usd)
                report = build_report(day, settings, app_conn=app_conn, store_conn=store_conn, http=http,
                                      cloudwatch=cloudwatch, cost_explorer=cost_explorer,
                                      cycle_days=cycle_days, today=today)
                if not args.dry_run:
                    persist(report, store_conn)
            reports.append(report)
            print(f"\n== {day} (revision {report.revision}, {report.billing_type}) ==")
            for col in COLUMNS:
                value = report.values.get(col)
                print(f"  {col}: {'-' if value is None else value}")

    if args.dry_run:
        print("\n[dry-run] nothing written")
        return 0
    if args.csv:
        written, skipped = write_csv(args.csv, reports, replace_existing=args.replace_existing)
        print(f"CSV {args.csv}: wrote {written or 'none'}; skipped existing {skipped or 'none'}")
    if args.xlsx_template:
        out = args.xlsx_out or (args.xlsx_template if args.in_place else
                                args.xlsx_template.with_name(args.xlsx_template.stem + "_updated.xlsx"))
        written, skipped = append_to_workbook(args.xlsx_template, out, reports,
                                              replace_existing=args.replace_existing, in_place=args.in_place)
        print(f"Workbook {out}: wrote {written or 'none'}; skipped existing {skipped or 'none'}")
    return 0
