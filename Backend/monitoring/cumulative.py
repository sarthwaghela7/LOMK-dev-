"""Month-to-date billing figures -> daily charges.

A provider dashboard that only shows a running total for the billing period must
never be copied into a day as if it were that day's cost. Snapshots store the
cumulative total through a day; the daily charge is the difference between two
consecutive snapshots of the SAME billing period. Re-saving a snapshot for the
same (provider, metric, period, day) replaces it, so a delayed or corrected
figure updates the result without double counting.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from .models import ACTUAL, Metric, Window, now_utc, unavailable


def period_start_for(day: date, cycle_day: int = 1) -> date:
    """Start of the billing period containing ``day`` (cycle_day clamped to 28)."""
    cycle_day = max(1, min(int(cycle_day), 28))
    if day.day >= cycle_day:
        return day.replace(day=cycle_day)
    prev_month_last = day.replace(day=1) - timedelta(days=1)
    return prev_month_last.replace(day=cycle_day)


def save_snapshot(conn, provider, metric, period_start: date, as_of: date, cumulative_usd: float, at=None):
    conn.execute(text("""
        INSERT INTO provider_cumulative_snapshots
          (provider, metric, period_start, as_of_date, cumulative_usd, collected_at)
        VALUES (:p, :m, :ps, :d, :v, :at)
        ON CONFLICT (provider, metric, period_start, as_of_date)
        DO UPDATE SET cumulative_usd = excluded.cumulative_usd, collected_at = excluded.collected_at
    """), {"p": provider, "m": metric, "ps": period_start.isoformat(), "d": as_of.isoformat(),
           "v": float(cumulative_usd), "at": at or now_utc()})


def _get(conn, provider, metric, period_start, as_of):
    row = conn.execute(text("""
        SELECT cumulative_usd, collected_at FROM provider_cumulative_snapshots
        WHERE provider=:p AND metric=:m AND period_start=:ps AND as_of_date=:d
    """), {"p": provider, "m": metric, "ps": period_start.isoformat(), "d": as_of.isoformat()}).first()
    return row


def daily_from_cumulative(conn, provider, metric, window: Window, cycle_day=1, source=None) -> tuple[Metric, str | None]:
    """Return (Metric, warning). Never returns a cumulative as a daily value."""
    day = window.day
    src = source or f"{provider} month-to-date snapshots (daily difference)"
    ps = period_start_for(day, cycle_day)
    today_row = _get(conn, provider, metric, ps, day)
    if today_row is None:
        why = f"no month-to-date snapshot for {day}; enter it with --mtd {provider}=<amount>"
        return unavailable(window, src, why), f"{provider} cost: {why}"
    if day == ps:
        delta, note = today_row[0], f"first day of billing period starting {ps}"
    else:
        prev = _get(conn, provider, metric, ps, day - timedelta(days=1))
        if prev is None:
            why = f"no snapshot for {day - timedelta(days=1)}; a daily charge needs two consecutive snapshots"
            return unavailable(window, src, why), f"{provider} cost: {why}"
        delta, note = today_row[0] - prev[0], f"MTD {today_row[0]:.4f} - MTD {prev[0]:.4f} (period from {ps})"
    if delta < -1e-9:
        why = f"month-to-date decreased by {abs(delta):.4f} (credit/refund/reset?); not reported as a daily charge"
        return unavailable(window, src, why), f"{provider} cost: {why}"
    at = today_row[1]
    if isinstance(at, str):
        at = datetime.fromisoformat(at)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return Metric(round(max(delta, 0.0), 6), ACTUAL, src, at, window.start, window.end,
                  note + "; fixed monthly fees billed in advance land on one day"), None
