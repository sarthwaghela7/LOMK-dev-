from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import text

from monitoring import costing, cumulative, quality
from monitoring.config import Settings
from monitoring.models import ACTUAL, ESTIMATED, MEASURED, Metric, Window, now_utc, unavailable

W = Window(date(2026, 10, 8), "UTC")


def S(**kw):
    return replace(Settings(), **kw)


def M(v, kind=MEASURED):
    return Metric(v, kind, "t", now_utc(), W.start, W.end)


# ---- costing -----------------------------------------------------------------
def test_llm_cost_prefers_actual_provider_figure():
    actual = M(9.99, ACTUAL)
    assert costing.llm_cost(actual, M(1), M(1), M(0), S(llm_price_input_per_1m=1, llm_price_output_per_1m=1), W) is actual


def test_llm_cost_estimate_from_tokens_with_cached_discount():
    cfg = S(llm_price_input_per_1m=1.0, llm_price_cached_input_per_1m=0.1, llm_price_output_per_1m=4.0)
    m = costing.llm_cost(None, M(2_000_000), M(500_000), M(1_000_000), cfg, W)
    assert m.kind == ESTIMATED and m.value == pytest.approx(1.0 + 0.1 + 2.0)


def test_llm_cost_without_cached_price_is_upper_bound_and_noted():
    m = costing.llm_cost(None, M(1_000_000), M(0), M(500_000), S(llm_price_input_per_1m=2, llm_price_output_per_1m=8), W)
    assert m.value == 2.0 and "upper bound" in m.note


def test_no_prices_means_unavailable_never_a_guess():
    assert costing.llm_cost(None, M(5), M(5), M(0), S(), W).value is None
    assert costing.stt_cost(M(60.0), S(), W).value is None
    assert costing.stt_cost(unavailable(W, "x", "no ledger"), S(stt_price_per_hour=1), W).value is None
    assert costing.stt_cost(M(90.0), S(stt_price_per_hour=0.4), W).value == pytest.approx(0.6)


def test_supabase_fixed_fee_prorated_by_days_in_month():
    snap = unavailable(W, "snap", "none")
    assert costing.supabase_cost(snap, S(supabase_fixed_monthly_usd=31), W).value == 1.0  # October has 31 days
    assert costing.supabase_cost(snap, S(), W).value is None
    actual = M(0.7, ACTUAL)
    assert costing.supabase_cost(actual, S(supabase_fixed_monthly_usd=31), W) is actual


def test_total_sums_only_present_components_and_labels_type():
    full = {k: M(1.0, ACTUAL) for k in costing.COST_KEYS}
    assert costing.total_and_type(full) == (6.0, "Actual", [])
    full["stt_cost"] = M(1.0, ESTIMATED)
    assert costing.total_and_type(full)[1] == "Estimated"
    partial = {**full, "railway_cost": unavailable(W, "r", "x"), "llm_cost": M(2.0, ACTUAL)}
    total, kind, missing = costing.total_and_type(partial)
    assert total == 6.0 and kind == "Estimated" and missing == ["Railway"]  # a gap is never reported as "Actual"
    none = {k: unavailable(W, "r", "x") for k in costing.COST_KEYS}
    assert costing.total_and_type(none)[0:2] == (None, "Not Available")


def test_cost_per_stt_call_denominator():
    assert costing.cost_per_stt_call(10.0, 4) == 2.5
    assert costing.cost_per_stt_call(10.0, 0) is None and costing.cost_per_stt_call(None, 4) is None


# ---- cumulative / billing periods ----------------------------------------------
def test_period_start():
    assert cumulative.period_start_for(date(2026, 10, 8)) == date(2026, 10, 1)
    assert cumulative.period_start_for(date(2026, 10, 8), 15) == date(2026, 9, 15)
    assert cumulative.period_start_for(date(2026, 10, 15), 15) == date(2026, 10, 15)
    assert cumulative.period_start_for(date(2026, 1, 3), 20) == date(2025, 12, 20)


def snap(conn, day, usd):
    cumulative.save_snapshot(conn, "railway", "cost", cumulative.period_start_for(day), day, usd)


def test_daily_cost_is_difference_of_consecutive_snapshots_not_the_cumulative(engine):
    with engine.begin() as c:
        snap(c, date(2026, 10, 7), 10.0); snap(c, date(2026, 10, 8), 10.75)
        m, warn = cumulative.daily_from_cumulative(c, "railway", "cost", W)
    assert m.value == 0.75 and m.kind == ACTUAL and warn is None   # NOT 10.75


def test_delayed_correction_replaces_snapshot_without_double_counting(engine):
    with engine.begin() as c:
        snap(c, date(2026, 10, 7), 10.0); snap(c, date(2026, 10, 8), 10.5)
        snap(c, date(2026, 10, 8), 10.9)   # provider revised the figure; same key
        assert c.execute(text("SELECT count(*) FROM provider_cumulative_snapshots")).scalar() == 2
        assert cumulative.daily_from_cumulative(c, "railway", "cost", W)[0].value == pytest.approx(0.9)


def test_missing_previous_snapshot_or_decrease_is_unavailable(engine):
    with engine.begin() as c:
        snap(c, date(2026, 10, 8), 10.0)
        m, warn = cumulative.daily_from_cumulative(c, "railway", "cost", W)
        assert m.value is None and "two consecutive" in warn
        snap(c, date(2026, 10, 7), 12.0)    # a credit/refund lowered the MTD
        m, warn = cumulative.daily_from_cumulative(c, "railway", "cost", W)
        assert m.value is None and "decreased" in warn
        m, warn = cumulative.daily_from_cumulative(c, "supabase", "cost", W)   # nothing entered
        assert m.value is None and "--mtd supabase" in warn


def test_first_day_of_period_uses_snapshot_and_never_spans_periods(engine):
    first = Window(date(2026, 10, 1), "UTC")
    with engine.begin() as c:
        snap(c, date(2026, 9, 30), 99.0)          # previous period must not be subtracted
        snap(c, date(2026, 10, 1), 0.4)
        assert cumulative.daily_from_cumulative(c, "railway", "cost", first)[0].value == 0.4


# ---- quality ------------------------------------------------------------------
def hist(*totals, stt=0):
    return [{"Total Daily Cost (USD)": t, "STT Failures": stt, "LLM Failures": 0} for t in totals]


def test_cost_spike_flag_and_floor():
    v = {"Total Daily Cost (USD)": 10.0}
    assert any("COST SPIKE" in f for f in quality.evaluate_flags(v, hist(2, 2, 2.5, 2), S()))
    assert not quality.evaluate_flags({"Total Daily Cost (USD)": 0.9}, hist(0.1, 0.1, 0.1), S())  # below min USD
    assert not quality.evaluate_flags(v, hist(2, 2), S())                                         # not enough history
    assert not quality.evaluate_flags({"Total Daily Cost (USD)": None}, hist(2, 2, 2), S())


def test_failure_increase_flag():
    v = {"STT Failures": 9, "LLM Failures": 0}
    assert any("STT Failures" in f for f in quality.evaluate_flags(v, hist(1, 1, 1, stt=1), S()))
    assert not quality.evaluate_flags({"STT Failures": 2}, hist(1, 1, 1, stt=0), S())  # under min count


def test_reconcile():
    assert quality.reconcile("tokens", 100, 150)
    assert quality.reconcile("tokens", 100, 105) is None
    assert quality.reconcile("tokens", None, 5) is None and quality.reconcile("t", 0, 0) is None
