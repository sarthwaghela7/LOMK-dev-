"""Read-only provider access probe (``--check-providers``). Prints PASS/FAIL per
provider with the reason, never keys or response bodies."""
from .models import Window
from .providers import aws_s3, elevenlabs, openai_usage, railway
from datetime import datetime, timedelta


def run_checks(settings, http, cloudwatch, cost_explorer) -> int:
    window = Window((datetime.now(settings.tz) - timedelta(days=2)).date(), settings.report_tz)
    failures = 0

    def show(name, metric_or_none, warnings):
        nonlocal failures
        ok = metric_or_none is not None and metric_or_none.value is not None
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  -> {'; '.join(warnings) or (metric_or_none.note if metric_or_none else 'n/a')}"))

    r = openai_usage.fetch(settings, window, http=http)
    show("OpenAI costs API", r.metrics.get("llm_cost"), r.warnings)
    r = elevenlabs.fetch(settings, window, http=http)
    print(("PASS" if "provider_stt_minutes" in r.metrics else "SKIP/FAIL") + "  ElevenLabs usage (reconciliation only) " + "; ".join(r.warnings))
    r = railway.fetch_usage_metric(settings, window, http=http)
    show("Railway usage query", r.metrics.get("railway_usage"), r.warnings)
    if cloudwatch is not None:
        r = aws_s3.fetch(settings, window, cloudwatch=cloudwatch, cost_explorer=cost_explorer)
        show("S3 CloudWatch size", r.metrics.get("s3_storage_gb"), r.warnings)
        show("S3 Cost Explorer", r.metrics.get("s3_storage_cost"), r.warnings)
    print("Supabase billing/egress: no verified API (manual)")
    return 1 if failures else 0
