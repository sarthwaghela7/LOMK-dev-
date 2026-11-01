"""Every reported value is a Metric carrying its provenance."""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# kind: how much to trust the number.
MEASURED = "measured"        # counted from our own database / ledger
ACTUAL = "actual"            # provider-reported billed amount
ESTIMATED = "estimated"      # computed from usage x configured price
UNAVAILABLE = "unavailable"  # could not be obtained; value is None, never 0


@dataclass(frozen=True)
class Metric:
    value: float | int | str | None
    kind: str
    source: str
    collected_at: datetime
    period_start: datetime
    period_end: datetime
    note: str | None = None

    def to_json(self):
        return {
            "value": self.value, "kind": self.kind, "source": self.source,
            "collected_at": self.collected_at.isoformat(),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "note": self.note,
        }


@dataclass(frozen=True)
class Window:
    """One reporting day in the configured timezone, as a UTC interval."""
    day: date
    tz_name: str

    @property
    def start(self) -> datetime:
        tz = ZoneInfo(self.tz_name)
        return datetime.combine(self.day, time.min, tz).astimezone(timezone.utc)

    @property
    def end(self) -> datetime:
        tz = ZoneInfo(self.tz_name)
        return datetime.combine(self.day + timedelta(days=1), time.min, tz).astimezone(timezone.utc)

    @property
    def is_utc_aligned(self) -> bool:
        return self.start.hour == 0 and self.start.minute == 0 and self.end - self.start == timedelta(days=1)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def unavailable(window: Window, source: str, reason: str, at: datetime | None = None) -> Metric:
    return Metric(None, UNAVAILABLE, source, at or now_utc(), window.start, window.end, reason)


@dataclass
class ProviderResult:
    """What one provider integration returns for one day. ``metrics`` maps
    report keys to Metric; ``warnings`` are explicit, human-readable problems."""
    metrics: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
