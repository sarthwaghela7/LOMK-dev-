# FlintDesk / AutoFlint — LLM Usage Inventory

Last verified against the working tree on **2026-10-12** (branch `feat/dashboard-ops-metrics`, backend + frontend repos).
Every entry below was traced to code. Anything that could only be inferred is marked **UNVERIFIED**.

Scope searched: `AutoFlint_Backend` (all `*.py`, `*.mjs`, requirements, Dockerfile) and `FlintDesk/src`.
No secret values are recorded here; only environment-variable **names**.

---

## 0. How models are called (shared facts)

| Item | Finding |
|---|---|
| Provider SDKs | **None.** `requirements.txt` has no `openai`, `anthropic`, `langchain` or similar. All calls are plain `requests.post(...)` to a chat-completions-style URL. |
| Provider / endpoint | `LLM_URL` = `LLM_URL` env → else `LLM_BASE_URL` env → else `https://api.openai.com/v1/chat/completions` (`utils/config.py`). The default is OpenAI. **UNVERIFIED:** which URL the production environment actually sets. |
| Model | `LLM_MODEL` env, default **`gpt-5.4-mini`** (`utils/config.py`). One model for every LLM call; there is no per-feature model. **UNVERIFIED:** the production value. |
| API key | `LLM_API_KEY` env (required; callers raise/503 when missing). |
| Rate limits | `OPENAI_RPM_LIMIT` / `OPENAI_TPM_LIMIT` env, else a cached once-a-day probe, else safe defaults (60 RPM / 40 000 TPM) — `utils/openai_limits.py`. A process-wide `TokenBucketRateLimiter` (`utils/rate_limiter.py`) throttles `FDLLM` requests. |
| Retry env | `F3_LLM_MAX_RETRIES` (5), `F3_LLM_BACKOFF_BASE_SECONDS` (1.0), `F3_LLM_BACKOFF_MAX_SECONDS` (30.0), `F3_LLM_WORKERS` (≤3). |
| Embeddings / vector search | **None.** No embedding model, vector column, or semantic retrieval exists in either repo. |
| Not an LLM | Speech-to-text (ElevenLabs, see §9), `case_type_taxonomy.py` (rule-based), Lead matching ("Analyse" buttons on Leads), Dependencies "call-summary", Hannah AI routing (`services/hannah_ai_routing.py` — deterministic routing of calls), Vitel metadata voicemail override. |

The shared HTTP wrapper is `FDLLM._post_with_retry` (`services/Processing_pipeline/fd_llm_node.py`):
token-bucket throttle → POST (timeout 120 s) → on HTTP 429 exponential backoff + jitter honouring `Retry-After`,
shrinking the shared rate limit; `insufficient_quota` / `billing_hard_limit_reached` trips a 5-minute circuit breaker
(`LLMQuotaExhausted`); after `F3_LLM_MAX_RETRIES` raises `LLMRateLimitExhausted`.
Only `FDLLM` uses this wrapper. `LLM`, `/calls/analysis/.../manual-data-score` and `/ai/narrative` call `requests.post` directly (no retry).

---

## 1. FlintDesk Stage 1 call analysis (the main LLM use)

| | |
|---|---|
| Feature | AI intake extraction for each call: client name/type, lead source, email, urgency, case type, case worker, outcome, enquiry question, call summary |
| Frontend entry | Call review page (`ViewCall.jsx`) Analyse / Re-analyse → `analyseFlintDeskCall()` in `FlintDesk/src/data/api.js` → `POST /api/flintdesk/calls/{call_id}/analyse`. Also runs with no user action (see trigger). |
| Backend | `FDLLM.request_analysis` / `FDLLM.parse_response` in `services/Processing_pipeline/fd_llm_node.py`, driven by `F3Processor.process_record` (`services/Database_sync/f3.py`) |
| Endpoint | `POST /api/flintdesk/calls/{call_id}/analyse` (`routes/flintdesk/calls.py::analyse_flintdesk_call`; `reanalyse=true` needs a `request_id`) |
| Worker | `run_flintdesk_f3_worker` (`scheduler/jobs.py`), APScheduler cron every minute at second 30, `max_instances=1`; processes transcript-ready calls of the current Central date, FIFO |
| Model | The shared `LLM_MODEL` (§0), `temperature: 0`, `response_format: {"type": "json_object"}` |
| Input | A single user message: `FLINTDESK_ANALYSIS_PROMPT` + call metadata JSON (`call_id`, `src`, `dst`, `billsec`, `disposition`, `extension`, `extname`, `call_date`, `call_time`, `audio_s3_uri`, `processing_status`) + summary-length guidance (based on audio duration read from the recording) + the **full call transcript** (customer speech included) |
| Prompt location | Hard-coded: `FLINTDESK_ANALYSIS_PROMPT` in `fd_llm_node.py` (not DB-driven) |
| Output | JSON object with exactly: `fd_a_client_name, fd_a_client_type, fd_a_lead_source, fd_a_email_address, fd_a_urgency_flag, fd_a_case_type, fd_a_case_worker_assigned, fd_a_call_outcome, enquiry_details, fd_a_call_summary` (null when unknown) |
| Validation | Strips code fences, `json.loads`, must be an object; blank strings → null; missing keys → null; `fd_a_client_type` forced onto the six dropdown values (`normalize_client_type`) else null; `fd_a_client_name` nulled if it is a staff name or the call's agent (`validate_client_name`); for Vitel `VOICEMAIL` calls the model's voicemail verdict is overridden by metadata (`apply_metadata_voicemail`, `services/vitel_metadata.py`) |
| Storage | `call_analysis_results` (`fd_a_*`, `enquiry_details`, `raw_llm_json` JSONB); `calls.fd_processing_status`; durable request/response checkpoint in `analysis_jobs` (`services/analysis_jobs.py::execute_analysis`) so a saved model response is replayed instead of paying for a second request |
| Display | Calls page Stage 1 columns; Dashboard; Case Types + EQ; ViewCall |
| Trigger | A call has a committed transcript (STT stage F2 finished) and no existing result; or a user presses Analyse/Re-analyse |
| Errors | 429 retry/backoff + circuit breaker (above). `AnalysisBusy` / `AnalysisNeedsReview` → HTTP 409 and no new paid request. Other failures → HTTP 503 from the endpoint; the worker logs and leaves the saved job state. Failure never resets shared call flags or overwrites a saved AI response |
| Env vars | `LLM_API_KEY`, `LLM_URL`/`LLM_BASE_URL`, `LLM_MODEL`, `OPENAI_RPM_LIMIT`, `OPENAI_TPM_LIMIT`, `F3_LLM_*` |
| DB | `calls`, `call_analysis_results`, `analysis_jobs` |

## 2. FlintDesk Stage 2 AI automation (Self Action Item, Action Due Date, Follow Up Dates)

| | |
|---|---|
| Feature | AI-suggested Stage 2 fields on the Calls page |
| Gate | `FD_STAGE2_AI_ENABLED` env (default **false**, `utils/config.py::STAGE2_AI_ENABLED`). **UNVERIFIED:** whether production has it on. |
| Frontend entry | Settings → "AI Prompts (Stage 2)" panel (`Settings.jsx::AiPromptsPanel`; `getFdPrompts`, `saveFdPrompt`, `rollbackFdPrompt`); `regenerateStage2()` in `api.js` (no UI button was found calling it — **UNVERIFIED** if used) |
| Backend | (a) Live path: **rides on the Stage 1 request** — `FDLLM.build_context` appends `stage2_automation.build_stage2_block(...)`; the answer arrives under a `"stage2"` key and `F3Processor._with_stage2` / `extract_stage2` validate it. No extra paid call. (b) Regeneration: `FDLLM.generate_stage2` (own request, up to 2 attempts) |
| Endpoints | `POST /api/flintdesk/calls/{call_id}/regenerate-stage2?replace_manual=` (Team Lead/Admin; 409 when the flag is off or prompts missing); `GET/PUT /flintdesk/fd-prompts…`, `POST …/rollback/{version}` (`routes/flintdesk/fd_prompts.py`) |
| Worker | Same `run_flintdesk_f3_worker`; offline `ops/backfill_stage2.py` |
| Input | Stage 1 context (§1) plus three DB prompts, entry datetime (`calls.registered_at`, America/Chicago) and the latest allowed due date |
| Prompt location | Table `fd_prompts` (keys `stage2_self_action_item`, `action_due_date`, `follow_up_date`; versioned, one active per key; 60 s cache) via `services/fd_prompt_store.py`. The output contract is fixed in code (`OUTPUT_CONTRACT` in `services/stage2_automation.py`). No hard-coded fallback prompt: if a prompt is missing the step is skipped |
| Output | JSON: `self_action_item`, `no_action_required`, `action_due_date`, `client_response_pending`, `follow_up_date`, `follow_up_note`, `reasoning_summary` |
| Validation | `stage2_automation.resolve_result`: due date clamped to < 72 h after entry (never earlier than entry), follow-up only when a client response is pending (else `"N/A"`), generic action text rejected, length caps. Unusable output → `None`: Stage 1 is still saved, nothing fabricated |
| Storage | `call_analysis_results.fd_m_self_action_item`, `fd_m_action_due_date`, `fd_m_follow_up_dates`, `fd_stage2_ai_meta` (JSONB incl. prompt versions). An agent-typed value is never overwritten unless it is unchanged AI output, or `replace_manual=true` |
| Errors | As §1; regeneration returns 409/422/503 with "nothing was changed" |
| Env vars | `FD_STAGE2_AI_ENABLED` plus §0 |
| DB | `fd_prompts` (migration `069_create_fd_prompts.py`), `call_analysis_results` |

## 3. Case Types + EQ "AI-Generated Insights"

| | |
|---|---|
| Feature | Plain-English analysis under the Trends view |
| Frontend entry | `CaseTypeTrendsView.jsx` → `getCaseTypeTrendInsights()` → `GET /api/dashboard/case-types-trends/insights?period=…` (also `start_date`/`end_date` for the Custom range). Fetched automatically once a period with enquiries has loaded; the PDF export waits for it |
| Backend | `generate_insights` / `_call_model` in `services/case_type_trends.py` (uses `FDLLM()._post_with_retry`); route in `routes/flintdesk/dashboard_metrics.py` |
| Input | Only aggregates produced by the server (`insight_facts`): totals, case-type counts, **redacted** text of questions asked more than once, daily counts, period comparison, notes. No names, numbers or transcripts; one-off question text is never sent |
| Prompt | Hard-coded `SYSTEM_PROMPT` in `case_type_trends.py` |
| Output | JSON with exactly `key_finding, repeated_questions, case_type_insights[≤4], trend_interpretation, team_preparation[≤4], data_limitations` |
| Validation | `validate_insights`: exact key set, string/list types, ≤320 chars, **every number must appear in the facts** else rejected (one retry with a correction message); trend text is overwritten when the comparison is not comparable |
| Storage | Not in the database: in-process cache keyed by `snapshot_hash` (6 h TTL). Report figures themselves are computed in SQL and are not AI-generated |
| Errors | Never fails the report: endpoint always returns HTTP 200 with `available: false` and a message; UI shows "AI insights are temporarily unavailable" |
| Env vars | §0 |

## 3b. Priority Cases (RFE / I-130 / I-485 / H4 / H4EAD)

| | |
|---|---|
| Feature | Per-call classification of five immigration forms; powers the Priority Cases page |
| Backend | `services/priority_cases.py` (classify, validate, store, batch), `services/priority_cases_read.py` (API reads), `routes/flintdesk/priority_cases.py`; worker `run_priority_cases_worker` (`scheduler/jobs.py`); backfill `ops/backfill_priority_cases.py` |
| Model | The shared `LLM_MODEL` via `FDLLM._post_with_retry` (same rate limiter, 429 back-off and credit circuit breaker as F3). `temperature: 0`, `response_format: json_object`. No new provider |
| Input | System prompt (from `fd_prompts`) + fixed output contract + **the transcript only** (capped at 14 000 chars). No call metadata, names or phone numbers |
| Requests | **One** request evaluates all five forms. A second only if the first answer is unusable (malformed / missing form / invalid status) |
| Cost controls | No keyword gate: every call with a usable transcript is sent (the lexical helper is diagnostic only); calls not listed on the Calls page are not sent; an unchanged transcript + same prompt version is never re-sent; a failed analysis gets at most 3 attempts in total, 30 min apart; the page/API/export never call the model |
| Prompt location | `fd_prompts` key `priority_case_classification` (seeded by migration 074, versioned like the Stage 2 prompts). Output contract fixed in code (`OUTPUT_CONTRACT`). No hard-coded fallback: a missing prompt skips the work |
| Output | `{"forms": {"RFE"\|"I-130"\|"I-485"\|"H4"\|"H4EAD": {status: confirmed\|not_confirmed\|uncertain, evidence, reason, confidence}}}` |
| Validation | `parse_result`: all five forms required, status in the three values, JSON object only. `confirmed` without evidence, or whose evidence is not found in the transcript, is stored as `uncertain` (never confirmed) |
| Storage | `call_priority_analysis` (migration 074): status, source (`llm`/`prefilter`), transcript sha256, prompt version, result JSONB, model, input/output tokens, attempts, error. Never writes `calls` or `call_analysis_results` |
| Trigger | Worker every minute at :50 (own job; yields while F3 has fresh un-analysed transcripts; last 3 days from `PRIORITY_CASES_SINCE`); backfill is an explicit command |
| Env vars | `PRIORITY_CASES_ENABLED` (default on), `PRIORITY_CASES_SINCE` (default 2026-10-20 = deployment day), `PRIORITY_CASES_BATCH_LIMIT` (10), `PRIORITY_CASES_LOOKBACK_DAYS` (3) plus §0 |
| DB | `calls`, `call_priority_analysis`, `fd_prompts` |

## 4. Manual-data quality score

| | |
|---|---|
| Feature | Scores 0–10 how useful the agent-typed Stage 2–4 text is |
| Frontend entry | Calls page: automatic after Submit when the call completes (`runManualDataScore(call, {silent:true})`) and a per-row score button in the Actions column; Leads page (`Leads.jsx`) — both call `scoreManualData()` |
| Backend | `score_manual_data` in `routes/calls.py` — direct `requests.post`, `temperature: 0`, JSON response format, 120 s timeout, **no retry** |
| Endpoint | `POST /calls/analysis/{analysis_id}/manual-data-score` |
| Input | The non-empty manual fields (`fd_m_self_action_item`, due date, action status, dependency, follow-ups, blocker, case status, verification comment, attachment names, Google review) as JSON. No transcript |
| Prompt | Inline f-string in `score_manual_data` |
| Output | `{"score": number, "reason": "…"}` |
| Validation | Score clamped to 0–10 and rounded to 1 dp; reason capped at 1000 chars; any provider/shape error → HTTP 502 "Unable to score manual data with the LLM"; missing config → 503 |
| Storage | `call_analysis_results.manual_data_score`, `manual_data_score_reason`, `manual_data_scored_at`. When no manual data exists the score is set to 0 **without** calling the model |
| Display | Priority/score cell on Calls and Leads |

## 5. AutoFlint legacy analysis (`llm_node.py`)

| | |
|---|---|
| Feature | AutoFlint call analysis (not FlintDesk) |
| Backend | `LLM.request_analysis` in `services/Processing_pipeline/llm_node.py`, run by `F3Processor(application="autoflint")` and the `Pipeline` class (`pipeline.py`, used by F2) |
| Worker | `run_daily_autoflint_analysis`, cron 02:05 America/Chicago (`daily_autoflint_f3`) |
| Input | `LLM_NODE_SYSTEM_PROMPT` + metadata JSON + full transcript |
| Prompt | Built from DB table `prompts` (row 1 = instructions, rows 2+ = output fields) by `utils/prompt_config.py`; edited through `routes/prompts.py` (`/prompts`), which calls `reload_prompts()` |
| Output | Free JSON text keyed by the `prompts` rows; no `response_format`, `temperature: 0` |
| Validation / storage | `F3Processor._parse_auto_flint_response` → `call_analysis_results` (+ `processing_status`) |
| Errors | `raise_for_status`; failure recorded in `call_analysis_results.raw_llm_json` and `processing_status="failed"`; no retry/backoff in this class |
| Note | FlintDesk code explicitly does **not** call this node. Whether AutoFlint is still active in production is **UNVERIFIED** (the job is registered in `scheduler/jobs.py`) |

## 6. `/ai/narrative` proxy (report narratives)

`POST /ai/narrative` (`routes/ai_proxy.py`) forwards a caller-supplied `system_prompt` + `user_content` to the LLM
(`max_completion_tokens: 1500`, JSON response format, 120 s) and returns the raw text. Requires login.
**No caller exists in `FlintDesk/src`** (searched for `narrative`, `/ai/`); it may serve another client — **UNVERIFIED**.
Prompt, input and output shape are entirely the caller's. Errors: 500 (not configured), 400 (missing fields), 502 (provider error / empty reply).

## 7. Ops / maintenance scripts that call the model

| Script | Use |
|---|---|
| `ops/backfill_stage2.py` | Batch `FDLLM.generate_stage2` for existing calls (needs `fd_prompts`; ignores `FD_STAGE2_AI_ENABLED`) |
| `ops/backfill_client_type.py` | Batch `FDLLM` run to fill `fd_a_client_type` |
| `run_manual_f3_backfill_20260910.py` | Manual F3 backfill (uses `F3Processor`) |
| `utils/openai_limits.py::_probe_openai` | One `max_tokens: 1` "ping" chat call to read rate-limit headers; **only when `OPENAI_LIMIT_PROBE_ENABLED=true`** and no env limits are set |

## 8. Dead or disabled code

* `services/Processing_pipeline/call_quality_node.py` — an entire call-quality LLM node, **fully commented out**; nothing imports it.
* `routes/flintdesk_legacy.py` — an older direct `FDLLM().generate_flintdesk_analysis` endpoint (`POST /flintdesk/calls/{call_id}/analyse`). **No module imports it and `main.py` does not mount it**, so it is dead code today (verified by search).

## 9. Related AI that is **not** an LLM

* **Speech-to-text** — `services/Processing_pipeline/stt_node.py` (stage F2) and `POST /ai/transcribe`: ElevenLabs-style API (`STT_API_KEY`, `STT_MODEL`, `STT_BASE_URL`, `ELEVENLABS_CONCURRENCY_LIMIT`), diarised with "Front Desk"/"Client" labels; front-desk names from `fd_users` are sent as key terms. Output is stored in `calls.transcript`. Its result feeds every LLM call above.
* **Case-type grouping** — `services/case_type_taxonomy.py` classifies `fd_a_case_type` by rules, no model.
* **Priority / lead score** — `priorityEngine.js`, `leadScoreEngine.js`: deterministic formulas in the frontend.

## 10. Data sent to the provider

Stage 1/2 requests contain the full call transcript, the caller's spoken details, and call metadata (phone numbers `src`/`dst`, extension, agent name, S3 URI). Insights (§3) and manual-score (§4) requests contain no transcripts; §3 additionally redacts question text. Treat the LLM provider as a data processor for client conversations.

## 11. Open questions to confirm outside the repo

1. Production values of `LLM_URL`, `LLM_MODEL`, `FD_STAGE2_AI_ENABLED`, `OPENAI_*_LIMIT`.
2. Whether `/ai/narrative` has any live caller outside `FlintDesk/src`.
3. Whether the AutoFlint daily job (§5) is still expected to run in production.
