"""AWS S3 for the configured bucket only (AWS_S3_BUCKET_NAME).

Size: CloudWatch daily storage metric ``BucketSizeBytes`` filtered by this
bucket's BucketName dimension (no bucket listing, no object scan).
Cost: Cost Explorer, opt-in (MONITOR_AWS_COST_EXPLORER_ENABLED) because each
call is billed by AWS. Cost Explorer cannot filter by bucket unless the bucket
is cost-allocation-tagged, so without MONITOR_AWS_COST_TAG_* the figure is the
whole S3 service for the account and is labelled so.
Credits/refunds/tax are reported separately and excluded from the component
unless MONITOR_AWS_INCLUDE_ADJUSTMENTS=true.
"""
from datetime import timedelta

from ..models import ACTUAL, MEASURED, Metric, ProviderResult, Window, now_utc, unavailable

GIB = 1024 ** 3
SERVICE = "Amazon Simple Storage Service"


def _storage(settings, window, cloudwatch, at, result):
    src = "CloudWatch AWS/S3 BucketSizeBytes"
    if not settings.s3_bucket:
        result.metrics["s3_storage_gb"] = unavailable(window, src, "AWS_S3_BUCKET_NAME not set", at)
        return
    try:
        dims = [{"Name": "BucketName", "Value": settings.s3_bucket}]
        listed = cloudwatch.list_metrics(Namespace="AWS/S3", MetricName="BucketSizeBytes", Dimensions=dims)
        types = sorted({d["Value"] for m in listed.get("Metrics", []) for d in m["Dimensions"]
                        if d["Name"] == "StorageType"})
        if not types:
            raise RuntimeError("no BucketSizeBytes series for this bucket")
        total = 0.0
        for storage_type in types:
            stats = cloudwatch.get_metric_statistics(
                Namespace="AWS/S3", MetricName="BucketSizeBytes",
                Dimensions=dims + [{"Name": "StorageType", "Value": storage_type}],
                StartTime=window.start - timedelta(days=1), EndTime=window.end + timedelta(days=1),
                Period=86400, Statistics=["Average"])
            points = [p for p in stats.get("Datapoints", []) if window.start - timedelta(days=1) <= p["Timestamp"] < window.end + timedelta(days=1)]
            if not points:
                continue
            total += max(points, key=lambda p: p["Timestamp"])["Average"]
        if total <= 0:
            raise RuntimeError("no datapoints returned")
        result.metrics["s3_storage_gb"] = Metric(round(total / GIB, 4), MEASURED, src, at, window.start, window.end,
                                                 "GiB; CloudWatch publishes this once a day (may be ~1 day behind)")
    except Exception as exc:  # noqa: BLE001
        reason = f"{type(exc).__name__}: {str(exc)[:120]}"
        result.metrics["s3_storage_gb"] = unavailable(window, src, reason, at)
        result.warnings.append(f"S3 size unavailable: {reason}")


def _costs(settings, window, ce, at, result):
    src = "AWS Cost Explorer (SERVICE=S3, DAILY, UnblendedCost)"
    keys = ("s3_storage_cost", "s3_request_cost")
    if not settings.aws_cost_explorer_enabled:
        for k in keys:
            result.metrics[k] = unavailable(window, src, "Cost Explorer disabled (opt-in; AWS bills per API call)", at)
        return
    if not window.is_utc_aligned:
        for k in keys:
            result.metrics[k] = unavailable(window, src, f"AWS bills in UTC days; {window.tz_name} cannot be matched", at)
        return
    try:
        flt = {"Dimensions": {"Key": "SERVICE", "Values": [SERVICE]}}
        scope = "whole S3 service for the account (bucket not isolated)"
        if settings.aws_cost_tag_key and settings.aws_cost_tag_value:
            flt = {"And": [flt, {"Tags": {"Key": settings.aws_cost_tag_key, "Values": [settings.aws_cost_tag_value]}}]}
            scope = f"tag {settings.aws_cost_tag_key}={settings.aws_cost_tag_value}"
        response = ce.get_cost_and_usage(
            TimePeriod={"Start": window.day.isoformat(), "End": (window.day + timedelta(days=1)).isoformat()},
            Granularity="DAILY", Metrics=["UnblendedCost"], Filter=flt,
            GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}, {"Type": "DIMENSION", "Key": "RECORD_TYPE"}])
        storage = requests_transfer = adjustments = 0.0
        groups = [g for r in response.get("ResultsByTime", []) for g in r.get("Groups", [])]
        for group in groups:
            usage_type, record_type = group["Keys"]
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            if record_type != "Usage" and not settings.aws_include_adjustments:
                adjustments += amount
                continue
            if "TimedStorage" in usage_type:
                storage += amount
            else:
                requests_transfer += amount
        if not groups:
            reason = "Cost Explorer returned no line items (billing data can lag up to 24h)"
            for k in keys:
                result.metrics[k] = unavailable(window, src, reason, at)
            result.warnings.append(f"S3 cost: {reason}")
            return
        note = f"scope: {scope}"
        if adjustments:
            note += f"; excluded credits/refunds/tax/fees net {adjustments:+.4f} USD"
        result.metrics["s3_storage_cost"] = Metric(round(storage, 6), ACTUAL, src, at, window.start, window.end, note)
        result.metrics["s3_request_cost"] = Metric(round(requests_transfer, 6), ACTUAL, src, at, window.start, window.end, note)
    except Exception as exc:  # noqa: BLE001
        reason = f"{type(exc).__name__}: {str(exc)[:120]}"
        for k in keys:
            result.metrics[k] = unavailable(window, src, reason, at)
        result.warnings.append(f"S3 cost unavailable: {reason}")


def fetch(settings, window: Window, *, cloudwatch, cost_explorer) -> ProviderResult:
    result, at = ProviderResult(), now_utc()
    _storage(settings, window, cloudwatch, at, result)
    _costs(settings, window, cost_explorer, at, result)
    return result
