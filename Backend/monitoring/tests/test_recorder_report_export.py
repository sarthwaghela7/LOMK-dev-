import csv
from dataclasses import replace
from datetime import date

import openpyxl
import pytest
from sqlalchemy import text

from monitoring import cumulative, export, recorder
from monitoring.config import Settings
from monitoring.report import COLUMNS, build_report, persist
from .conftest import FakeHttp, FakeResponse, add_call, add_event, add_job, utc

DAY = date(2026, 10, 8)
IN = utc(2026, 10, 8, 12)


# ---- recorder ---------------------------------------------------------------
def count(engine):
    with engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM provider_usage_events")).scalar()


def test_record_event_is_idempotent_on_event_key(engine):
    assert recorder.record_event("openai", "chat_completion", "success", event_key="k1", engine=engine)
    assert recorder.record_event("openai", "chat_completion", "success", event_key="k1", engine=engine)
    assert count(engine) == 1


def test_record_event_never_raises_even_with_broken_database():
    class Broken:
        def begin(self):
            raise RuntimeError("db down")
    assert recorder.record_event("openai", "x", "success", engine=Broken()) is False


def test_usage_extraction_keeps_missing_as_none():
    u = recorder.usage_from_chat_response({"usage": {"prompt_tokens": 7, "completion_tokens": 3,
                                                      "prompt_tokens_details": {"cached_tokens": 2}}})
    assert u == {"input_tokens": 7, "output_tokens": 3, "cached_input_tokens": 2}
    assert recorder.usage_from_chat_response({"choices": []}) == {"input_tokens": None, "output_tokens": None, "cached_input_tokens": None}
    assert recorder.usage_from_chat_response(None)["input_tokens"] is None


def test_stt_scope_records_success_then_failure_and_reraises(engine, monkeypatch):
    monkeypatch.setattr(recorder, "_engine", lambda: engine)
    with recorder.stt_usage_scope("call-1", model="scribe"):
        recorder.report_stt_audio_seconds(42.5)
    with pytest.raises(ValueError):
        with recorder.stt_usage_scope("call-2"):
            recorder.report_stt_audio_seconds(10)
            raise ValueError("boom")
    with engine.connect() as c:
        rows = c.execute(text("SELECT call_id,status,audio_seconds,error_class FROM provider_usage_events ORDER BY id")).all()
    assert rows == [("call-1", "success", 42.5, None), ("call-2", "failed", 10, "ValueError")]


def test_report_audio_outside_scope_is_a_noop():
    recorder.report_stt_audio_seconds(5)  # must not raise


def test_record_llm_response(engine, monkeypatch):
    monkeypatch.setattr(recorder, "_engine", lambda: engine)
    recorder.record_llm_response(FakeResponse(200, {"model": "gpt-x", "usage": {"prompt_tokens": 5, "completion_tokens": 2}}))
    recorder.record_llm_response(object())  # malformed response: swallowed
    with engine.connect() as c:
        assert c.execute(text("SELECT model,input_tokens,output_tokens FROM provider_usage_events")).all() == [("gpt-x", 5, 2)]


# ---- end-to-end report with mocked providers ---------------------------------
def seed(engine):
    with engine.begin() as c:
        for i in range(5):
            add_call(c, f"c{i}", IN)
        add_event(c, "s1", IN, call_id="c0", audio_seconds=600)
        add_event(c, "s2", IN, call_id="c1", audio_seconds=300)
        add_event(c, "s3", IN, call_id="c2", status="failed", error_class="Timeout")
        add_event(c, "l1", IN, provider="openai", operation="chat_completion", model="gpt-5.4-mini",
                  input_tokens=1_000_000, output_tokens=100_000, cached_input_tokens=0)
        add_job(c, "j1", "c0", "completed", IN)
        add_job(c, "j2", "c1", "completed", IN)


def settings(**kw):
    return replace(Settings(), stt_price_per_hour=0.4, llm_price_input_per_1m=0.5, llm_price_output_per_1m=2.0, **kw)


def run(engine, cfg, http=None, **kw):
    with engine.connect() as app, engine.begin() as store:
        report = build_report(DAY, cfg, app_conn=app, store_conn=store, http=http or FakeHttp(), cloudwatch=None, **kw)
        persist(report, store)
    return report


def test_report_end_to_end_values_and_provenance(engine):
    seed(engine)
    r = run(engine, settings())
    v = r.values
    assert (v["Calls Ingested"], v["Calls STT Completed"], v["Audio Minutes Sent to STT"]) == (5, 2, 15.0)
    assert (v["LLM Analyses Completed"], v["LLM Input Tokens"], v["LLM Output Tokens"]) == (2, 1_000_000, 100_000)
    assert v["STT Cost (USD)"] == pytest.approx(0.1) and v["LLM Cost (USD)"] == pytest.approx(0.7)
    assert v["Total Daily Cost (USD)"] == pytest.approx(0.8)            # only components that exist
    assert v["Cost per STT-Completed Call (USD)"] == pytest.approx(0.4)
    assert (v["STT Failures"], v["LLM Failures"]) == (1, 0)
    assert v["Railway Cost (USD)"] is None and v["Supabase Egress (GB)"] is None  # blanks, not zeros
    assert v["Billing Data Type"] == "Estimated"
    assert "PARTIAL TOTAL" in v["Notes / Issues"] and "NOT zero" in v["Notes / Issues"]
    assert set(COLUMNS) >= {k for k in v}
    assert r.metrics["stt_cost"].kind == "estimated" and r.metrics["llm_input_tokens"].source


def test_rerun_updates_one_row_and_bumps_revision(engine):
    seed(engine)
    run(engine, settings())
    r2 = run(engine, settings())
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*), max(revision) FROM usage_daily_reports")).one() == (1, 2)
    assert r2.revision == 2


def test_delayed_provider_data_updates_without_double_counting(engine):
    seed(engine)
    cfg = settings(openai_admin_key="k")
    down = FakeHttp({"costs": FakeResponse(200, {"data": []}), "usage": FakeResponse(200, {"data": []})})
    first = run(engine, cfg, down)
    assert first.metrics["llm_cost"].kind == "estimated"          # fell back to tokens x rate
    up = FakeHttp({"costs": FakeResponse(200, {"data": [{"results": [{"amount": {"value": 0.65, "currency": "usd"}}]}]}),
                   "usage": FakeResponse(200, {"data": []})})
    second = run(engine, cfg, up)
    assert second.values["LLM Cost (USD)"] == 0.65 and second.metrics["llm_cost"].kind == "actual"
    assert second.values["Total Daily Cost (USD)"] == pytest.approx(0.75)  # replaced, not 0.7 + 0.65


def test_transient_provider_failure_keeps_earlier_actual_value(engine):
    seed(engine)
    cfg = settings(openai_admin_key="k")
    good = FakeHttp({"costs": FakeResponse(200, {"data": [{"results": [{"amount": {"value": 0.65, "currency": "usd"}}]}]}),
                     "usage": FakeResponse(200, {"data": []})})
    run(engine, replace(cfg, llm_price_input_per_1m=None), good)
    bad = FakeHttp({"costs": FakeResponse(500), "usage": FakeResponse(500)})
    r = run(engine, replace(cfg, llm_price_input_per_1m=None), bad)
    assert r.values["LLM Cost (USD)"] == 0.65 and "retained from revision 1" in r.metrics["llm_cost"].note
    assert any("kept value" in w for w in r.warnings)


def test_mtd_snapshots_flow_into_daily_cost(engine):
    seed(engine)
    with engine.begin() as c:
        cumulative.save_snapshot(c, "railway", "cost", date(2026, 10, 1), date(2026, 10, 7), 20.0)
        cumulative.save_snapshot(c, "railway", "cost", date(2026, 10, 1), DAY, 20.9)
    r = run(engine, settings())
    assert r.values["Railway Cost (USD)"] == pytest.approx(0.9)


def test_reconciliation_warning_when_provider_disagrees(engine):
    seed(engine)
    http = FakeHttp({"costs": FakeResponse(200, {"data": []}),
                     "usage/completions": FakeResponse(200, {"data": [{"results": [{"input_tokens": 2_000_000, "output_tokens": 100_000}]}]})})
    r = run(engine, settings(openai_admin_key="k"), http)
    assert any("RECONCILIATION" in w and "input tokens" in w for w in r.warnings)


def test_no_secrets_or_content_in_values(engine):
    seed(engine)
    r = run(engine, settings(openai_admin_key="sk-admin-TOPSECRET", railway_token="rw-SECRET"))
    blob = repr(r.values) + repr(r.warnings) + repr({k: m.to_json() for k, m in r.metrics.items()})
    assert "SECRET" not in blob


# ---- export -----------------------------------------------------------------
def fake_report(engine, day=DAY):
    seed(engine)
    return run(engine, settings())


def test_csv_appends_and_never_duplicates_a_date(engine, tmp_path):
    rep = fake_report(engine)
    path = tmp_path / "r.csv"
    assert export.write_csv(path, [rep]) == (["2026-10-08"], [])
    assert export.write_csv(path, [rep]) == ([], ["2026-10-08"])      # silent duplicate prevented, reported
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == 1 and list(rows[0].keys()) == COLUMNS
    assert rows[0]["Railway Cost (USD)"] == ""                         # blank, not 0
    assert export.write_csv(path, [rep], replace_existing=True) == (["2026-10-08"], [])
    assert len(list(csv.DictReader(path.open()))) == 1


def make_tracker(path, rows=5):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Daily Tracker"
    ws.append(COLUMNS)
    for r in range(2, rows + 2):
        ws.cell(r, 18).value = f'=IF(A{r}="","",SUM(E{r},I{r},J{r},L{r},P{r},Q{r}))'
        ws.cell(r, 19).value = f'=IF(OR(A{r}="",C{r}="",C{r}=0),"",R{r}/C{r})'
    wb.save(path)


def test_workbook_append_keeps_formulas_template_and_history(engine, tmp_path):
    rep = fake_report(engine)
    tpl, out = tmp_path / "t.xlsx", tmp_path / "out.xlsx"
    make_tracker(tpl)
    wb = openpyxl.load_workbook(tpl)
    wb["Daily Tracker"]["A2"] = openpyxl.utils.datetime.from_ISO8601("2026-10-07")   # an existing historic row
    wb["Daily Tracker"]["L2"] = 1.23
    wb.save(tpl)
    before = tpl.read_bytes()
    assert export.append_to_workbook(tpl, out, [rep]) == (["2026-10-08"], [])
    assert tpl.read_bytes() == before                                  # template untouched
    ws = openpyxl.load_workbook(out)["Daily Tracker"]
    assert ws["L2"].value == 1.23 and ws["A3"].value.date() == DAY     # history kept; appended after last used row
    assert ws["C3"].value == 2 and ws["J3"].value is None              # blank, not zero
    assert ws["R3"].value.startswith("=IF(A3") and ws["S3"].value.startswith("=IF(OR(A3")  # formulas intact
    # duplicate date is skipped, not appended
    assert export.append_to_workbook(out, tmp_path / "out2.xlsx", [rep]) == ([], ["2026-10-08"])


def test_workbook_replace_never_clears_manual_cells(engine, tmp_path):
    rep = fake_report(engine)
    tpl, out = tmp_path / "t.xlsx", tmp_path / "o.xlsx"
    make_tracker(tpl)
    export.append_to_workbook(tpl, out, [rep])
    wb = openpyxl.load_workbook(out)
    wb["Daily Tracker"]["J2"] = 4.5             # operator typed Railway cost by hand
    wb.save(out)
    out2 = tmp_path / "o2.xlsx"
    assert export.append_to_workbook(out, out2, [rep], replace_existing=True) == (["2026-10-08"], [])
    assert openpyxl.load_workbook(out2)["Daily Tracker"]["J2"].value == 4.5   # None never overwrites


def test_workbook_refuses_in_place_without_flag_and_wrong_header(engine, tmp_path):
    rep = fake_report(engine)
    tpl = tmp_path / "t.xlsx"
    make_tracker(tpl)
    with pytest.raises(ValueError):
        export.append_to_workbook(tpl, tpl, [rep])
    export.append_to_workbook(tpl, tpl, [rep], in_place=True)
    assert list(tmp_path.glob("t.backup-*.xlsx"))                      # backup made first
    wb = openpyxl.Workbook(); wb.active.title = "Daily Tracker"; wb.active.append(["Wrong"]); wb.save(tmp_path / "bad.xlsx")
    with pytest.raises(ValueError):
        export.append_to_workbook(tmp_path / "bad.xlsx", tmp_path / "x.xlsx", [rep])
