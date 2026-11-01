"""Supabase: size comes from SQL (db_size.py); billing/egress have no verified API.

Supabase documents a Management API with project analytics (request counts) but
no endpoint for billed cost or egress was found. Both are therefore reported as
unavailable (manual dashboard entry) rather than approximated; cost can instead
be derived from operator-entered month-to-date snapshots or a configured fixed
plan fee (estimate), see costing.py.
"""
from ..models import ProviderResult, Window, now_utc, unavailable


def fetch(settings, window: Window) -> ProviderResult:
    result, at = ProviderResult(), now_utc()
    result.metrics["supabase_egress"] = unavailable(
        window, "Supabase", "no verified API for egress; read it from Dashboard > Settings > Usage", at)
    result.warnings.append("Supabase egress: not available via API (manual dashboard entry)")
    return result
