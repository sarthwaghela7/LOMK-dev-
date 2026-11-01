from dataclasses import replace
from datetime import date, datetime, timezone

from monitoring.config import Settings
from monitoring.models import ACTUAL, MEASURED, UNAVAILABLE, Window
from monitoring.providers import aws_s3, elevenlabs, openai_usage, railway
from .conftest import FakeHttp, FakeResponse

W = Window(date(2026, 10, 8), "UTC")


def S(**kw):
    return replace(Settings(), **kw)


# ---- OpenAI -----------------------------------------------------------------
def costs_body(*amounts, currency="usd"):
    return {"data": [{"results": [{"amount": {"value": a, "currency": currency}} for a in amounts]}]}


def test_openai_cost_is_actual_and_summed():
    http = FakeHttp({"costs": FakeResponse(200, costs_body(1.25, 0.5)),
                     "usage/completions": FakeResponse(200, {"data": [{"results": [{"input_tokens": 10, "output_tokens": 4}]}]})})
    r = openai_usage.fetch(S(openai_admin_key="sk-admin-SECRET"), W, http=http)
    assert r.metrics["llm_cost"].value == 1.75 and r.metrics["llm_cost"].kind == ACTUAL
    assert r.metrics["provider_llm_input_tokens"].value == 10


def test_openai_missing_key_and_empty_bucket_are_unavailable_not_zero():
    r = openai_usage.fetch(S(), W, http=FakeHttp())
    assert r.metrics["llm_cost"].value is None and r.warnings
    r = openai_usage.fetch(S(openai_admin_key="k"), W, http=FakeHttp({"costs": FakeResponse(200, {"data": []}),
                                                                      "usage": FakeResponse(200, {"data": []})}))
    assert r.metrics["llm_cost"].kind == UNAVAILABLE and "lag" in r.metrics["llm_cost"].note


def test_openai_http_error_never_leaks_key_or_body():
    http = FakeHttp({"costs": FakeResponse(401, {"error": "bad key sk-admin-SECRET"}), "usage": FakeResponse(401)})
    r = openai_usage.fetch(S(openai_admin_key="sk-admin-SECRET"), W, http=http)
    text = repr(r.metrics) + repr(r.warnings)
    assert "SECRET" not in text and "HTTP 401" in text


def test_openai_non_utc_window_refused_and_non_usd_refused():
    r = openai_usage.fetch(S(openai_admin_key="k"), Window(date(2026, 10, 8), "America/Chicago"), http=FakeHttp())
    assert r.metrics["llm_cost"].value is None and "UTC" in r.metrics["llm_cost"].note
    r = openai_usage.fetch(S(openai_admin_key="k"), W, http=FakeHttp({"costs": FakeResponse(200, costs_body(1, currency="eur")),
                                                                      "usage": FakeResponse(200, {"data": []})}))
    assert r.metrics["llm_cost"].value is None


# ---- ElevenLabs --------------------------------------------------------------
def test_elevenlabs_disabled_is_silent_enabled_reconciles():
    assert elevenlabs.fetch(S(), W, http=FakeHttp()).metrics == {}
    http = FakeHttp({"character-stats": FakeResponse(200, {"time": [1], "usage": {"All": [12.5]}})})
    r = elevenlabs.fetch(S(elevenlabs_usage_enabled=True, elevenlabs_api_key="x"), W, http=http)
    assert r.metrics["provider_stt_minutes"].value == 12.5 and r.metrics["provider_stt_minutes"].kind == MEASURED
    r = elevenlabs.fetch(S(elevenlabs_usage_enabled=True, elevenlabs_api_key="x"), W,
                         http=FakeHttp({"character-stats": FakeResponse(403)}))
    assert "provider_stt_minutes" not in r.metrics and r.warnings


# ---- Railway ----------------------------------------------------------------
def test_railway_usage_and_graphql_errors():
    cfg = S(railway_token="t", railway_project_id="p")
    ok = FakeHttp({"railway": FakeResponse(200, {"data": {"usage": [{"measurement": "CPU_USAGE", "value": 3.2}]}})})
    assert railway.fetch_usage_metric(cfg, W, http=ok).metrics["railway_usage"].value == "CPU_USAGE=3.2"
    bad = FakeHttp({"railway": FakeResponse(200, {"errors": [{"message": "Cannot query field usage"}]})})
    r = railway.fetch_usage_metric(cfg, W, http=bad)
    assert r.metrics["railway_usage"].value is None and "Cannot query" in r.warnings[0]
    assert railway.fetch_usage_metric(S(), W, http=FakeHttp()).metrics["railway_usage"].value is None


# ---- AWS S3 -----------------------------------------------------------------
class FakeCloudWatch:
    def __init__(self, size_bytes, types=("StandardStorage",)):
        self.size, self.types, self.dims_seen = size_bytes, types, []

    def list_metrics(self, **kw):
        self.dims_seen.append(kw["Dimensions"])
        return {"Metrics": [{"Dimensions": kw["Dimensions"] + [{"Name": "StorageType", "Value": t}]} for t in self.types]}

    def get_metric_statistics(self, **kw):
        return {"Datapoints": [{"Timestamp": datetime(2026, 10, 8, tzinfo=timezone.utc), "Average": self.size}]}


class FakeCE:
    def __init__(self, groups):
        self.groups, self.kwargs = groups, None

    def get_cost_and_usage(self, **kw):
        self.kwargs = kw
        return {"ResultsByTime": [{"Groups": [{"Keys": list(k), "Metrics": {"UnblendedCost": {"Amount": str(a)}}} for k, a in self.groups]}]}


def test_s3_size_is_scoped_to_configured_bucket_and_summed():
    cw = FakeCloudWatch(2 * 1024 ** 3, types=("StandardStorage", "StandardIAStorage"))
    r = aws_s3.fetch(S(s3_bucket="my-bucket"), W, cloudwatch=cw, cost_explorer=None)
    assert r.metrics["s3_storage_gb"].value == 4.0
    assert cw.dims_seen == [[{"Name": "BucketName", "Value": "my-bucket"}]]  # never enumerates other buckets


def test_s3_costs_split_and_adjustments_excluded():
    ce = FakeCE([(("APS2-TimedStorage-ByteHrs", "Usage"), 0.02), (("APS2-Requests-Tier1", "Usage"), 0.003),
                 (("DataTransfer-Out-Bytes", "Usage"), 0.1), (("APS2-TimedStorage-ByteHrs", "Credit"), -0.02),
                 (("Tax", "Tax"), 0.01)])
    r = aws_s3.fetch(S(s3_bucket="b", aws_cost_explorer_enabled=True), W, cloudwatch=FakeCloudWatch(1), cost_explorer=ce)
    assert r.metrics["s3_storage_cost"].value == 0.02 and r.metrics["s3_request_cost"].value == 0.103
    assert r.metrics["s3_storage_cost"].kind == ACTUAL and "excluded credits" in r.metrics["s3_storage_cost"].note
    assert "whole S3 service" in r.metrics["s3_storage_cost"].note
    r = aws_s3.fetch(S(s3_bucket="b", aws_cost_explorer_enabled=True, aws_include_adjustments=True), W,
                     cloudwatch=FakeCloudWatch(1), cost_explorer=ce)
    assert r.metrics["s3_storage_cost"].value == 0.0  # credit nets the usage


def test_s3_cost_disabled_empty_and_error_are_unavailable():
    r = aws_s3.fetch(S(s3_bucket="b"), W, cloudwatch=FakeCloudWatch(1), cost_explorer=None)
    assert r.metrics["s3_storage_cost"].value is None
    r = aws_s3.fetch(S(s3_bucket="b", aws_cost_explorer_enabled=True), W, cloudwatch=FakeCloudWatch(1), cost_explorer=FakeCE([]))
    assert r.metrics["s3_request_cost"].value is None and any("lag" in w for w in r.warnings)

    class Boom:
        def get_cost_and_usage(self, **kw):
            raise RuntimeError("AccessDenied")
    r = aws_s3.fetch(S(s3_bucket="b", aws_cost_explorer_enabled=True), W, cloudwatch=FakeCloudWatch(1), cost_explorer=Boom())
    assert r.metrics["s3_storage_cost"].value is None and any("AccessDenied" in w for w in r.warnings)


def test_s3_cost_tag_filter_applied():
    ce = FakeCE([(("TimedStorage", "Usage"), 1)])
    aws_s3.fetch(S(s3_bucket="b", aws_cost_explorer_enabled=True, aws_cost_tag_key="app", aws_cost_tag_value="flint"),
                 W, cloudwatch=FakeCloudWatch(1), cost_explorer=ce)
    assert "And" in ce.kwargs["Filter"]
