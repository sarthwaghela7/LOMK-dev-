"""Priority Cases: spelling-tolerant candidate detection, strict model-output validation, and the classify() request loop.

No database and no network: the model is a deterministic stub.
"""
import json

import pytest
from fastapi import HTTPException

from middleware.auth import require_fd_priority_cases
from services import priority_cases as pc


# ---------------------------------------------------------------- keyword hints (diagnostic only: they never gate a request)
@pytest.mark.parametrize("text,form", [
    ("I got an RFE last week", "RFE"), ("it was an rfe", "RFE"), ("a R.F.E. notice", "RFE"), ("an R F E came", "RFE"),
    ("Request for Evidence arrived", "RFE"), ("a request for additional evidence", "RFE"),
    ("my I-130 is pending", "I-130"), ("the i 130 petition", "I-130"), ("filed I130 in May", "I-130"), ("i-130 status", "I-130"),
    ("I  130 receipt", "I-130"), ("eye 130 filing", "I-130"), ("I one thirty is pending", "I-130"),
    ("I-485 pending", "I-485"), ("the I 485 case", "I-485"), ("my I485", "I-485"), ("i-485 interview", "I-485"),
    ("i four eighty five filed", "I-485"), ("adjustment of status application", "I-485"),
    ("H4 visa", "H4"), ("H-4 stamping", "H4"), ("my H 4 extension", "H4"), ("h4 dependent", "H4"), ("h-4 renewal", "H4"),
    ("H4EAD renewal", "H4EAD"), ("H4 EAD card", "H4EAD"), ("H-4 EAD pending", "H4EAD"), ("H 4 EAD", "H4EAD"),
    ("H4-EAD", "H4EAD"), ("H-4EAD", "H4EAD"), ("h4ead", "H4EAD"), ("h4 ead", "H4EAD"), ("her employment authorization", "H4EAD"),
])
def test_spelling_variants_are_candidates(text, form):
    assert form in pc.candidate_forms(text)


@pytest.mark.parametrize("text", [
    "I need help with my citizenship interview", "she works at the school on the 4th floor",
    "the shipment arrived in March", "call me back tomorrow", "my number is 8 4 7", "which 4 appointments do you have",
    "", None,
])
def test_unrelated_text_is_not_a_candidate(text):
    assert pc.candidate_forms(text) == set()


def test_f3_case_type_hint_widens_but_never_narrows():
    assert "I-485" in pc.candidate_forms("garbled audio ...", hints="I-485 / Green Card")
    assert pc.candidate_forms("I-485 pending", hints="") >= {"I-485"}


# ---------------------------------------------------------------- response validation
TRANSCRIPT = ("Agent: How can I help? Client: I received an RFE for my I-485 last week and my husband is on H-4. "
              "Agent: Did you file the I-130 for your mother? Client: No, that was years ago in 2015, not this time.")


def answer(**overrides):
    forms = {f: {"status": "not_confirmed", "evidence": None, "reason": None, "confidence": None} for f in pc.FORMS}
    forms.update(overrides)
    return json.dumps({"forms": forms})


def test_confirmed_with_verbatim_evidence_is_kept_and_multiple_forms_allowed():
    result = pc.parse_result(answer(
        **{"RFE": {"status": "confirmed", "evidence": "I received an RFE for my I-485", "reason": "caller got an RFE", "confidence": "high"},
           "I-485": {"status": "confirmed", "evidence": "RFE for my I-485 last week", "reason": "pending I-485", "confidence": "medium"}}),
        TRANSCRIPT)
    assert pc.forms_with_status(result, "confirmed") == ["RFE", "I-485"]
    assert result["forms"]["RFE"]["confidence"] == "high"
    assert set(result["forms"]) == set(pc.FORMS)


def test_evidence_matching_ignores_case_spacing_and_punctuation():
    result = pc.parse_result(answer(**{"H4": {"status": "confirmed", "evidence": "my HUSBAND is on h 4", "reason": "r"}}), TRANSCRIPT)
    assert result["forms"]["H4"]["status"] == "confirmed"


def test_invented_evidence_is_downgraded_to_uncertain_and_not_kept():
    result = pc.parse_result(answer(**{"H4EAD": {"status": "confirmed", "evidence": "she needs her H4 EAD renewed", "reason": "x"}}), TRANSCRIPT)
    assert result["forms"]["H4EAD"]["status"] == "uncertain"
    assert "Downgraded" in result["forms"]["H4EAD"]["reason"]


def test_confirmed_without_evidence_is_never_confirmed():
    result = pc.parse_result(answer(**{"RFE": {"status": "confirmed", "evidence": None, "reason": "r"}}), TRANSCRIPT)
    assert result["forms"]["RFE"]["status"] == "uncertain"


def test_uncertain_keeps_evidence_for_review_only_when_it_is_real():
    ok = pc.parse_result(answer(**{"I-130": {"status": "uncertain", "evidence": "Did you file the I-130", "reason": "history?"}}), TRANSCRIPT)
    assert ok["forms"]["I-130"]["evidence"] == "Did you file the I-130"
    fake = pc.parse_result(answer(**{"I-130": {"status": "uncertain", "evidence": "not in the call", "reason": "?"}}), TRANSCRIPT)
    assert fake["forms"]["I-130"]["evidence"] is None and fake["forms"]["I-130"]["status"] == "uncertain"


def test_not_confirmed_drops_any_evidence():
    result = pc.parse_result(answer(**{"I-130": {"status": "not_confirmed", "evidence": "I-130", "reason": "history", "confidence": "high"}}), TRANSCRIPT)
    assert result["forms"]["I-130"] == {"status": "not_confirmed", "evidence": None, "reason": None, "confidence": None}


def test_status_spelling_is_normalised_and_form_keys_are_forgiving():
    body = {"forms": {"rfe": {"status": "Not Confirmed"}, "I130": {"status": "not-confirmed"}, "i 485": {"status": "NOT_CONFIRMED"},
                      "H-4": {"status": "not_confirmed"}, "H4 EAD": {"status": "not_confirmed"}}}
    result = pc.parse_result(json.dumps(body), TRANSCRIPT)
    assert all(result["forms"][f]["status"] == "not_confirmed" for f in pc.FORMS)


@pytest.mark.parametrize("content", [
    "", None, "not json at all", "[]", json.dumps({"nope": 1}), json.dumps({"forms": []}),
    json.dumps({"forms": {"RFE": {"status": "not_confirmed"}}}),                                             # four forms missing
    answer(RFE={"status": "maybe"}), answer(RFE={"status": None}), answer(RFE="confirmed"),                   # invalid status / shape
])
def test_malformed_or_incomplete_output_is_rejected(content):
    with pytest.raises(pc.InvalidModelOutput):
        pc.parse_result(content, TRANSCRIPT)


def test_markdown_fences_are_tolerated_but_text_around_json_is_not():
    assert pc.parse_result("```json\n" + answer() + "\n```", TRANSCRIPT)
    with pytest.raises(pc.InvalidModelOutput):
        pc.parse_result("Sure! " + answer(), TRANSCRIPT)


# ---------------------------------------------------------------- request building
PROMPT = {"version": 3, "system_prompt": "Classify forms.", "user_prompt_template": "Transcript:\n{{transcript}}"}


def test_request_contains_only_prompt_contract_and_transcript():
    messages = pc.build_messages(PROMPT, "  hello H-4 world  ")
    assert messages[0]["role"] == "system" and messages[0]["content"].startswith("Classify forms.")
    assert pc.OUTPUT_CONTRACT in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "Transcript:\nhello H-4 world"}


def test_transcript_is_capped():
    messages = pc.build_messages(PROMPT, "x" * (pc.MAX_TRANSCRIPT_CHARS + 5000))
    assert len(messages[1]["content"]) <= pc.MAX_TRANSCRIPT_CHARS + 40


class StubResponse:
    def __init__(self, content, usage=None):
        self._body = {"choices": [{"message": {"content": content}}], "usage": usage or {"prompt_tokens": 700, "completion_tokens": 120}}

    def json(self):
        return self._body


class StubLLM:
    model = "stub-model"

    def __init__(self, *outputs):
        self.outputs, self.requests = list(outputs), []

    def _post_with_retry(self, payload):
        self.requests.append(payload)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return StubResponse(out)


def test_classify_one_request_for_all_five_forms_and_reports_usage():
    llm = StubLLM(answer(**{"RFE": {"status": "confirmed", "evidence": "I received an RFE", "reason": "r", "confidence": "high"}}))
    result, usage = pc.classify(llm, PROMPT, TRANSCRIPT)
    assert len(llm.requests) == 1
    assert llm.requests[0]["response_format"] == {"type": "json_object"} and llm.requests[0]["temperature"] == 0
    assert usage == {"input_tokens": 700, "output_tokens": 120}
    assert pc.forms_with_status(result, "confirmed") == ["RFE"]


def test_classify_retries_once_on_malformed_output_then_succeeds():
    llm = StubLLM("garbage", answer())
    result, usage = pc.classify(llm, PROMPT, TRANSCRIPT)
    assert len(llm.requests) == 2 and usage["input_tokens"] == 1400
    assert pc.forms_with_status(result, "confirmed") == []


def test_classify_gives_up_after_two_unusable_answers():
    llm = StubLLM("garbage", "still garbage")
    with pytest.raises(pc.InvalidModelOutput):
        pc.classify(llm, PROMPT, TRANSCRIPT)
    assert len(llm.requests) == 2


def test_provider_errors_propagate_without_a_second_request():
    llm = StubLLM(RuntimeError("boom"), answer())
    with pytest.raises(RuntimeError):
        pc.classify(llm, PROMPT, TRANSCRIPT)
    assert len(llm.requests) == 1


def test_transcript_hash_changes_with_the_transcript_only():
    assert pc.transcript_hash("a b") == pc.transcript_hash("a b") != pc.transcript_hash("a b ")


# ---------------------------------------------------------------- access control
@pytest.mark.parametrize("role", ["admin", "team_lead", "team lead", "Team Lead", "subject_matter_expert", "Subject Matter Expert", "sme", "superuser", "super_user"])
def test_priority_cases_allows_team_lead_sme_admin(role):
    user = {"role": role}
    assert require_fd_priority_cases(user) is user


@pytest.mark.parametrize("role", ["agent", "", "viewer", "front desk agent", None])
def test_priority_cases_rejects_other_roles(role):
    with pytest.raises(HTTPException) as exc:
        require_fd_priority_cases({"role": role})
    assert exc.value.status_code == 403


# ---------------------------------------------------------------- contextual prompt (next fd_prompts version)
def test_contextual_prompt_is_valid_for_the_prompt_store_and_keeps_the_contract_separate():
    from services import fd_prompt_store
    from services import priority_case_prompt as new
    assert fd_prompt_store.validate_prompt_fields(new.SYSTEM_PROMPT, new.USER_PROMPT_TEMPLATE) is None
    assert "{{transcript}}" in new.USER_PROMPT_TEMPLATE
    assert "Return ONLY" not in new.SYSTEM_PROMPT            # the fixed OUTPUT_CONTRACT is appended by code, not duplicated
    messages = pc.build_messages({"system_prompt": new.SYSTEM_PROMPT, "user_prompt_template": new.USER_PROMPT_TEMPLATE}, "hello")
    assert messages[0]["content"].endswith(pc.OUTPUT_CONTRACT) and "hello" in messages[1]["content"]


def test_contextual_prompt_instructions():
    from services import priority_case_prompt as new
    text = new.SYSTEM_PROMPT
    for form in pc.FORMS:
        assert form in text
    for required in ("does NOT have to be named", "never, by itself, a reason for not_confirmed", "independently",
                     "confirmed", "not_confirmed", "uncertain", "word for word", "at most 300 characters",
                     "never invent evidence", "speech-to-text", "eye 130", "I four eighty five", "H four", "H4 EAD",
                     "H4EAD can be confirmed without H4"):
        assert required in text, required
    assert str(pc.MAX_EVIDENCE_CHARS) in text


# ---------------------------------------------------------------- the validated model answer is final (hints never override)
def test_model_answer_is_not_overridden_by_keywords_in_the_transcript():
    transcript = "She asked what an RFE is in general, hypothetically. Nothing is filed for me."
    assert "RFE" in pc.candidate_forms(transcript)
    result = pc.parse_result(answer(), transcript)
    assert pc.forms_with_status(result, "confirmed") == [] and pc.forms_with_status(result, "uncertain") == []


def test_contextual_match_without_any_form_name_is_accepted_with_verbatim_evidence():
    transcript = "Client: USCIS sent me a letter asking for more documents about my case, the deadline is in thirty days."
    assert pc.candidate_forms(transcript) == set()           # no keyword at all
    result = pc.parse_result(answer(RFE={"status": "confirmed", "reason": "USCIS asked for more documents",
                                         "evidence": "USCIS sent me a letter asking for more documents about my case",
                                         "confidence": "medium"}), transcript)
    assert pc.forms_with_status(result, "confirmed") == ["RFE"]


def test_multiple_contextual_forms_and_ambiguity():
    transcript = ("Client: My wife is on a dependent visa through my H-1B and she wants to work. "
                  "Agent: Is that the work permit? Client: I think so, and I also filed something for my mother.")
    result = pc.parse_result(answer(**{
        "H4EAD": {"status": "confirmed", "evidence": "she wants to work", "reason": "H-4 spouse wants a work permit", "confidence": "medium"},
        "H4": {"status": "uncertain", "evidence": "dependent visa through my H-1B", "reason": "dependent status mentioned", "confidence": "low"},
        "I-130": {"status": "uncertain", "evidence": "I also filed something for my mother", "reason": "unclear petition", "confidence": "low"},
    }), transcript)
    assert pc.forms_with_status(result, "confirmed") == ["H4EAD"]
    assert pc.forms_with_status(result, "uncertain") == ["I-130", "H4"] or set(pc.forms_with_status(result, "uncertain")) == {"H4", "I-130"}


def test_evidence_is_capped_at_300_characters():
    transcript = "x " * 400 + "RFE"
    long_evidence = ("x " * 200).strip()
    result = pc.parse_result(answer(RFE={"status": "confirmed", "evidence": long_evidence, "reason": "r", "confidence": "low"}), transcript)
    assert len(result["forms"]["RFE"]["evidence"]) <= pc.MAX_EVIDENCE_CHARS
