import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from middleware.auth import require_fd_admin
from services import stage2_automation as s2
from services.stage_completion import missing_stage2_fields
from services.fd_prompt_store import validate_prompt_fields

TZ = s2.BUSINESS_TZ


def entry(y=2026, m=10, d=10, h=15, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


# ---------------- Action Due Date: strictly < 72h after entry
@pytest.mark.parametrize("hour,minute", [(0, 0), (9, 30), (15, 0), (23, 59)])
def test_max_due_date_is_entry_plus_two_days(hour, minute):
    e = entry(h=hour, mi=minute)
    assert s2.max_action_due_date(e) == date(2026, 10, 12)


def test_max_due_date_across_dst_change():
    # US DST ends 2026-11-01; the 72h window is 73 wall-clock hours long.
    assert s2.max_action_due_date(entry(2026, 10, 31, 15)) == date(2026, 11, 2)
    assert s2.max_action_due_date(entry(2026, 11, 1, 0, 0)) == date(2026, 11, 2)


def test_due_date_is_never_later_than_limit_even_if_model_asks():
    e = entry()
    for raw in ("2026-10-13", "2026-12-31", "2099-01-01"):
        value, status = s2.resolve_due_date(raw, e)
        assert value == date(2026, 10, 12) and status == "clamped"


def test_due_date_before_entry_is_raised_to_entry_date():
    assert s2.resolve_due_date("2026-10-01", entry()) == (date(2026, 10, 10), "clamped")


@pytest.mark.parametrize("raw", [None, "", "10/12/2026", "2026-02-30", "2026-13-01", "tomorrow", 20261012, "2026-10-12T00:00"])
def test_malformed_due_dates_use_deterministic_fallback_within_limit(raw):
    value, status = s2.resolve_due_date(raw, entry())
    assert status == "fallback" and value == date(2026, 10, 11)


def test_valid_due_date_kept():
    assert s2.resolve_due_date("2026-10-11", entry()) == (date(2026, 10, 11), "ok")


def test_entry_time_comes_from_registered_at_not_processing_date():
    call = SimpleNamespace(registered_at=datetime(2026, 1, 5, 20, 0, tzinfo=TZ), call_date=date(2026, 1, 5), call_time=None)
    assert s2.entry_datetime(call).date() == date(2026, 1, 5)
    assert s2.max_action_due_date(s2.entry_datetime(call)) == date(2026, 1, 7)


# ---------------- Follow-up
def test_no_pending_response_means_na_even_if_model_returns_a_date():
    assert s2.resolve_follow_up(False, "2026-10-14", entry()) == ("N/A", "none_pending")
    assert s2.resolve_follow_up(None, "2026-10-14", entry()) == ("N/A", "none_pending")
    assert s2.resolve_follow_up("true", "2026-10-14", entry())[0] == "N/A"  # not a real boolean


def test_pending_response_gets_formatted_date():
    assert s2.resolve_follow_up(True, "2026-10-14", entry()) == ("10-14-2026", "ok")


def test_follow_up_is_relative_to_entry_date_and_bounded():
    e = entry()
    assert s2.resolve_follow_up(True, "2026-10-10", e) == ("10-11-2026", "clamped")
    assert s2.resolve_follow_up(True, "2027-01-01", e) == ("11-09-2026", "clamped")
    assert s2.resolve_follow_up(True, "garbage", e) == ("10-13-2026", "fallback")


# ---------------- Action item validation / whole-result resolution
@pytest.mark.parametrize("bad", [None, "", "   ", 5, "Follow up with the client.", "Contact the client", "Take necessary action"])
def test_generic_or_empty_action_items_rejected(bad):
    assert s2.validate_action_item(bad) is None


def test_specific_action_item_accepted_and_length_capped():
    text = "Confirm the client's H-1B approval notice and expiration date, then route to the attorney. " * 40
    assert len(s2.validate_action_item(text)) == s2.MAX_ACTION_ITEM_CHARS


def test_result_rejected_without_action_item():
    assert s2.resolve_result({"self_action_item": "  "}, entry()) is None
    assert s2.resolve_result(None, entry()) is None
    assert s2.resolve_result([], entry()) is None


def test_internal_attorney_review_does_not_create_follow_up():
    result = s2.resolve_result({
        "self_action_item": "Route the I-485 status question to the attorney for review of the file.",
        "action_due_date": "2026-10-11", "client_response_pending": False,
        "follow_up_date": "2026-10-14", "follow_up_note": "should be dropped",
    }, entry())
    assert result["fd_m_follow_up_dates"] == "N/A" and result["follow_up_note"] is None
    assert result["dependency_client"] is False


def test_pending_client_document_creates_follow_up_and_note():
    result = s2.resolve_result({
        "self_action_item": "Obtain the client's I-797 approval notice, then route to intake review.",
        "action_due_date": "2026-10-11", "client_response_pending": True,
        "follow_up_date": "2026-10-14", "follow_up_note": "Check that the I-797 was received.",
    }, entry())
    assert result["fd_m_follow_up_dates"] == "10-14-2026"
    assert result["follow_up_note"] and result["dependency_client"] is True
    assert result["fd_m_action_due_date"] == "2026-10-11"


def test_no_action_required_does_not_invent_work():
    result = s2.resolve_result({"no_action_required": True, "self_action_item": "x", "action_due_date": "2026-10-11"}, entry())
    assert result["fd_m_action_due_date"] is None and result["fd_m_follow_up_dates"] == "N/A"


def test_model_json_parsing_handles_fences_and_garbage():
    assert s2.parse_model_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert s2.parse_model_json("not json") is None
    assert s2.parse_model_json("[1]") is None


def test_messages_use_prompts_and_never_embed_secrets():
    prompts = {k: {"system_prompt": f"SYS-{k}", "user_prompt_template": "Entry {{entry_date}} max {{max_action_due_date}}", "version": 3}
               for k in ("stage2_self_action_item", "action_due_date", "follow_up_date")}
    call = SimpleNamespace(call_id="c1", src="1", dst="2", billsec=60, disposition="ANSWERED", extname="A")
    system, user = s2.build_messages(prompts, "hello transcript", call, entry())
    assert "SYS-action_due_date" in system["content"] and "OUTPUT FORMAT" in system["content"]
    assert "hello transcript" in user["content"] and "max 2026-10-12" in user["content"]
    assert "{{" not in user["content"]


# ---------------- Persistence and edit protection
def _analysis(**kw):
    base = dict(fd_m_self_action_item=None, fd_m_action_due_date=None, fd_m_follow_up_dates=None,
                fd_m_action_due_note=None, fd_m_dependency=None, fd_stage2_ai_meta=None)
    base.update(kw)
    return SimpleNamespace(**base)


RESULT = {"fd_m_self_action_item": "AI item", "fd_m_action_due_date": "2026-10-11",
          "fd_m_follow_up_dates": "10-14-2026", "follow_up_note": "note", "dependency_client": True}


def test_first_generation_fills_blank_fields_and_records_provenance():
    a = _analysis()
    written = s2.apply_stage2_result(a, RESULT, {"stage2_self_action_item_version": 2})
    assert set(written) == set(s2.STAGE2_FIELDS)
    assert a.fd_m_action_due_date == date(2026, 10, 11) and a.fd_m_dependency == "Client"
    assert a.fd_stage2_ai_meta["fd_m_self_action_item"]["stage2_self_action_item_version"] == 2


def test_manual_edit_is_not_overwritten_by_reprocessing():
    a = _analysis()
    s2.apply_stage2_result(a, RESULT, {})
    a.fd_m_self_action_item = "Agent rewrote this"
    a.fd_m_follow_up_dates = "10-20-2026"
    new = {**RESULT, "fd_m_self_action_item": "AI v2", "fd_m_follow_up_dates": "10-15-2026", "fd_m_action_due_date": "2026-10-12"}
    written = s2.apply_stage2_result(a, new, {})
    assert a.fd_m_self_action_item == "Agent rewrote this" and a.fd_m_follow_up_dates == "10-20-2026"
    assert written == ["fd_m_action_due_date"]  # untouched AI value may be refreshed
    assert a.fd_m_action_due_date == date(2026, 10, 12)


def test_explicit_replace_manual_overwrites():
    a = _analysis(fd_m_self_action_item="typed by agent")
    s2.apply_stage2_result(a, RESULT, {}, replace_manual=True)
    assert a.fd_m_self_action_item == "AI item"


def test_agent_value_without_ai_history_is_protected():
    a = _analysis(fd_m_self_action_item="typed", fd_m_follow_up_dates="10-20-2026")
    s2.apply_stage2_result(a, RESULT, {})
    assert a.fd_m_self_action_item == "typed" and a.fd_m_follow_up_dates == "10-20-2026"


# ---------------- Stage 2 gate / Stage 4 optional
def test_na_follow_up_satisfies_stage2_gate():
    record = SimpleNamespace(fd_m_self_action_item="x", fd_m_manual_action_status="Open",
                             fd_m_dependency="None", fd_m_follow_up_dates="N/A")
    assert missing_stage2_fields(record) == []


def test_stage4_fields_are_not_required_by_the_stage2_gate():
    from services.stage_completion import STAGE2_REQUIRED_FIELDS
    assert "fd_m_stage4_attachment" not in STAGE2_REQUIRED_FIELDS
    assert "fd_m_google_review" not in STAGE2_REQUIRED_FIELDS


# ---------------- fd_prompts validation and authorization
def test_prompt_validation_rejects_empty_and_oversized():
    assert validate_prompt_fields("a", "b") is None
    assert validate_prompt_fields("  ", "b") and validate_prompt_fields("a", "") and validate_prompt_fields("a" * 20001, "b")


@pytest.mark.parametrize("role", ["agent", "team_lead", "subject_matter_expert", ""])
def test_prompt_writes_denied_to_non_admins(role):
    with pytest.raises(HTTPException) as exc:
        require_fd_admin({"role": role})
    assert exc.value.status_code == 403


def test_prompt_routes_are_admin_protected():
    from routes.flintdesk import fd_prompts as routes
    from middleware.auth import require_fd_settings_view
    deps = {}
    for route in routes.router.routes:
        deps[(tuple(route.methods)[0], route.path)] = [d.call for d in route.dependant.dependencies]
    assert all(require_fd_admin in calls for (method, _), calls in deps.items() if method in {"PUT", "POST"})
    assert all(require_fd_settings_view in calls for (method, _), calls in deps.items() if method == "GET")


def test_missing_prompt_means_no_stage2_generation(monkeypatch):
    from services import fd_prompt_store as store
    monkeypatch.setattr(store, "get_active_prompt", lambda key: None if key == "follow_up_date" else {"version": 1})
    assert store.get_stage2_prompts() is None


# ---------------- Stage 2 rides on the single Stage 1 request
PROMPTS = {k: {"system_prompt": f"SYS-{k}", "user_prompt_template": "Entry {{entry_date}} max {{max_action_due_date}}", "version": 4}
           for k in ("stage2_self_action_item", "action_due_date", "follow_up_date")}
CALL = SimpleNamespace(call_id="c1", src="1", dst="2", billsec=60, disposition="ANSWERED", extname="A", extension="1",
                       call_date=date(2026, 10, 10), call_time=None, audio_s3_uri=None, processing_status=None,
                       registered_at=datetime(2026, 10, 10, 15, 0, tzinfo=TZ))


def _llm(monkeypatch):
    from services.Processing_pipeline import fd_llm_node
    monkeypatch.setattr(fd_llm_node, "LLM_API_KEY", "k")
    monkeypatch.setattr(fd_llm_node, "LLM_URL", "http://x")
    llm = fd_llm_node.FDLLM()
    monkeypatch.setattr(llm, "audio_duration", lambda call: None)
    return llm


def test_stage1_request_unchanged_without_stage2_prompts(monkeypatch):
    context = _llm(monkeypatch).build_context("hello", CALL)
    assert "FRONT-DESK ACTION PLAN" not in context and "stage2" not in context


def test_stage2_section_is_appended_to_the_same_request(monkeypatch):
    context = _llm(monkeypatch).build_context("hello", CALL, PROMPTS)
    assert "CALL TRANSCRIPT:\nhello" in context
    assert context.count("CALL TRANSCRIPT:") == 1  # transcript sent once
    assert "SYS-follow_up_date" in context and "2026-10-12" in context and '"stage2"' in context


def test_f3_attaches_validated_stage2_from_single_response():
    from services.Database_sync.f3 import F3Processor
    content = json.dumps({"fd_a_client_name": "X", "stage2": {
        "self_action_item": "Obtain the client's I-797 notice and route to intake review.",
        "action_due_date": "2026-12-01", "client_response_pending": True, "follow_up_date": "2026-10-14"}})
    response = {"choices": [{"message": {"content": content}}]}
    out = F3Processor._with_stage2(CALL, response, PROMPTS)
    stage2 = out["_stage2"]
    assert stage2["fd_m_action_due_date"] == "2026-10-12"  # model's late date clamped by the backend
    assert stage2["fd_m_follow_up_dates"] == "10-14-2026"
    assert stage2["prompt_versions"]["action_due_date_version"] == 4


@pytest.mark.parametrize("content", ['{"fd_a_client_name": "X"}', '{"stage2": "bad"}', '{"stage2": {"self_action_item": ""}}', "not json"])
def test_missing_or_invalid_stage2_leaves_stage1_response_intact(content):
    from services.Database_sync.f3 import F3Processor
    response = {"choices": [{"message": {"content": content}}]}
    assert F3Processor._with_stage2(CALL, response, PROMPTS) == response


def test_no_prompts_means_no_stage2_work():
    from services.Database_sync.f3 import F3Processor
    response = {"choices": [{"message": {"content": "{}"}}]}
    assert F3Processor._with_stage2(CALL, response, None) is response


def test_stage1_parse_ignores_nested_stage2_key():
    from services.Processing_pipeline import fd_llm_node
    response = {"choices": [{"message": {"content": json.dumps({"fd_a_client_name": "Jane Roe", "stage2": {"a": 1}})}}]}
    parsed = fd_llm_node.FDLLM.parse_response(object.__new__(fd_llm_node.FDLLM), response, CALL)
    assert parsed["fd_a_client_name"] == "Jane Roe" and set(fd_llm_node.FD_AI_FIELDS) <= set(parsed)


# ---------------- Stage 2 only for calls listed on the Calls page
def _processor(monkeypatch, on_calls_page):
    from services.Database_sync import f3
    monkeypatch.setattr(f3, "STAGE2_AI_ENABLED", True)
    monkeypatch.setattr(f3, "get_stage2_prompts", lambda: PROMPTS)
    monkeypatch.setattr(f3, "is_on_calls_page", lambda call: on_calls_page)
    processor = object.__new__(f3.F3Processor)
    processor.application = "flintdesk"
    return processor


def test_stage2_requested_for_calls_listed_on_calls_page(monkeypatch):
    assert _processor(monkeypatch, True)._stage2_prompts(CALL) is PROMPTS


def test_stage2_skipped_for_missed_call_page_records(monkeypatch):
    assert _processor(monkeypatch, False)._stage2_prompts(CALL) is None


def test_calls_page_membership_fails_closed(monkeypatch):
    from services import calls_page_scope

    def boom(db, day):
        raise RuntimeError("db down")
    monkeypatch.setattr(calls_page_scope, "calls_page_call_ids", boom)
    assert calls_page_scope.is_on_calls_page(CALL) is False


def test_calls_page_membership_uses_listed_ids(monkeypatch):
    from services import calls_page_scope
    monkeypatch.setattr(calls_page_scope, "calls_page_call_ids", lambda db, day: {"c1"})
    assert calls_page_scope.is_on_calls_page(CALL) is True
    assert calls_page_scope.is_on_calls_page(SimpleNamespace(call_id="other", call_date=date(2026, 10, 10))) is False
