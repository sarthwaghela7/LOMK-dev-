"""Data-quality flags. Pure functions over today's values and prior reports."""
from statistics import median


def evaluate_flags(values: dict, history: list[dict], settings) -> list[str]:
    flags = []
    prior_totals = [h["Total Daily Cost (USD)"] for h in history if h.get("Total Daily Cost (USD)") is not None][-7:]
    total = values.get("Total Daily Cost (USD)")
    if total is not None and len(prior_totals) >= 3:
        base = median(prior_totals)
        if total >= settings.spike_min_usd and base > 0 and total > base * settings.spike_ratio:
            flags.append(f"COST SPIKE: {total:.2f} USD vs trailing median {base:.2f} USD")
    for col in ("STT Failures", "LLM Failures"):
        value = values.get(col)
        prior = [h[col] for h in history if h.get(col) is not None][-7:]
        if value is None or len(prior) < 3:
            continue
        base = median(prior)
        if value >= settings.failure_min_count and value > max(base, 1) * settings.failure_ratio:
            flags.append(f"FAILURE INCREASE: {col} = {value} vs trailing median {base:g}")
    return flags


def reconcile(label, ours, theirs, tolerance=0.10):
    """Warn when our measured figure and the provider's disagree by >10%."""
    if ours is None or theirs is None or max(ours, theirs) == 0:
        return None
    gap = abs(ours - theirs) / max(ours, theirs)
    if gap > tolerance:
        return f"RECONCILIATION: {label} app={ours:g} vs provider={theirs:g} ({gap:.0%} apart)"
    return None
