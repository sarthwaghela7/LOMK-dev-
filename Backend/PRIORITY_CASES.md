# Priority Cases

Calls whose transcripts discuss **RFE, I-130, I-485, H4 or H4EAD**, shown on a new **Priority Cases** page (sidebar, directly under *Case Types + EQ*) for Team Leads, SMEs and Admins.

The page only **reads saved results** and opens on today's date (Central). A background worker (new calls) and an explicit backfill command (history) do the classification. Opening the page, changing filters, refreshing or exporting never calls the model.

## How it works

```
F1 -> F2 (STT, stores calls.transcript) -> F3 (Calls-page analysis, unchanged)
                                   \
                                    -> priority_cases_worker (own job, every minute at :50)
                                         -> ONE model request for all five forms -> call_priority_analysis
GET /flintdesk/priority-cases  ->  reads call_priority_analysis for the Calls-page records in the date range
```

* **Separate from F3.** Own table, own status, own job. F3's prompt, output, `fd_processing_status`, `analysis_jobs` and `call_analysis_results` are never read for writing or changed. A priority failure cannot fail an F3 result; an F3 failure does not block priority analysis (the worker only *steps aside* while F3 still has fresh transcripts to analyse — at most about 30 minutes after a call registers).
* **Same LLM client as F3** (`FDLLM._post_with_retry`): shared token-bucket rate limit, 429 back-off, credit-exhausted circuit breaker. One request at a time, small batches (`PRIORITY_CASES_BATCH_LIMIT`, default 10 per minute).
* **Flexible detection.** **There is no keyword gate: every call with a usable transcript is sent to the model**, because a call can concern a form without naming it. A spelling-tolerant keyword helper still exists (`candidate_forms`) but is diagnostic only (the run counts `keyword_hint` / `no_keyword_hint`); it never skips a request and never decides a status. The model reads the context and returns `confirmed` / `not_confirmed` / `uncertain` per form, with a verbatim transcript excerpt. History, hypotheticals, denials and unrelated abbreviations are `not_confirmed`; unclear references are `uncertain`.
* **Evidence is verified.** A `confirmed` result whose evidence is missing or not found in the transcript is stored as `uncertain`. Only `confirmed` appears in the tables.
* **Cost controls.** Stored transcript + prompt version → never re-sent (transcript hash and prompt version are compared in SQL). No transcript → skipped and counted, never transcribed. Calls not listed on the Calls page (Missed Calls / routing legs) are not sent (stored `analysis_source = 'prefilter'`, the only remaining non-model outcome). A failed analysis is retried (30 minutes apart) up to 3 attempts in total, then stops until the transcript or prompt changes. Provider slow-down (429 / no credits) stops the batch and releases the claim without using an attempt.
* **Multi-instance safe.** Work is claimed with an atomic `INSERT … ON CONFLICT DO UPDATE` and a 15-minute lease; a crashed worker's claim expires and is picked up again.

## Definitions used on the page

| Column | Source |
|---|---|
| Call ID | `calls.call_reference` (AF#…) |
| Date / Time | `calls.call_date` / `call_time` (business date, Central) |
| Name / Email | client profile → manual → AI value, as on the Calls page |
| Phone | the client's number (far end of the call by direction); blank when direction can't be established |
| Case Type / Call Summary | `fd_a_case_type` (fallback `case_type`) / `fd_a_call_summary` (fallback `call_summary`) |
| Transcript | full stored transcript (viewer; capped at 20 000 characters in the API) |
| `<FORM> Found In` | **new definition** (none existed): where the form was found (`Transcript`) and the passage that shows it |
| Duplicates Merged | **new definition**: how many other legs of the same call (same date, time, caller and callee) the Calls page merged into this record. A form confirmed on any leg counts for the merged record |

"Pending", "failed" and "uncertain" calls are never shown as confirmed matches. The page header line shows analysed / waiting / failed / unclear counts for the range so "none found" can be told apart from "not analysed yet". The API can also return the uncertain items for review: `GET /flintdesk/priority-cases?…&result=uncertain`.

## API

`GET /flintdesk/priority-cases?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&form_type=all|RFE|I-130|I-485|H4|H4EAD[&result=confirmed|uncertain]`

* Roles: `admin`, `team_lead`, `subject_matter_expert` (and `superuser`) — enforced server-side (`require_fd_priority_cases`); everyone else 403, no token 401.
* Dates are inclusive calendar days (Central business dates). `end < start` → 400; more than 92 days → 400 (same limit as the Calls page); unknown `form_type`/`result` → 422.
* Scope = the Calls-page records (`analysis_read(call_source=True, target_page="calls")`): same exclusions and duplicate collapsing as the Calls page. Up to 1000 rows per form; `total` and `truncated` say if more exist.

## Database

Migration `074_priority_cases` (additive, idempotent, does no analysis and rewrites no data):

* table `call_priority_analysis` (one row per call, FK to `calls` with `ON DELETE CASCADE`, GIN index on `confirmed_forms`);
* seeds `fd_prompts` key `priority_case_classification` version 1 **only if that key has no rows**, so reruns never overwrite an admin's edit.

Rollback (nothing else depends on it): `DROP TABLE call_priority_analysis; DELETE FROM fd_prompts WHERE prompt_key = 'priority_case_classification';`

The prompt can be revised with `services.fd_prompt_store.save_new_version('priority_case_classification', …)` (versioned, one active). A new version re-analyses calls on the next worker run (last 3 days) or the next backfill run (any range). It is intentionally not shown in Settings → AI Prompts (that panel stays Stage 2 only).

## Deployment sequence

1. **Migration first, before the backend merge** (new code reads the new table): `python -m migrations.runner`. Only with the owner's approval; no dumps or backups against production.
2. Merge/deploy the backend. Confirm the startup log shows the scheduler job `priority_cases_worker` (`Job 'priority_cases_worker' next run: …`). The prompt lives in the database (seeded by the migration), so there are no prompt files to ship. `LLM_API_KEY`, `LLM_URL`, `LLM_MODEL` are the existing ones.
3. Deploy the frontend (Render). Admins, Team Leads and SMEs see **Priority Cases**; agents do not.
4. Verify automatic analysis: after a new call has been transcribed and analysed by F3, within a few minutes `call_priority_analysis` has a `completed` row for it (`analysis_source = 'llm'`; `'prefilter'` only for a Missed-Calls/routing leg) and worker lines `[priority-cases] {…}` appear in the log when there was work.
5. **Then** run the October backfill (below). It is never started by deployment, startup or the migration.
6. Check the page: header line coverage counts, tables, export.

Switches: `PRIORITY_CASES_ENABLED=false` stops the worker (default on). `PRIORITY_CASES_SINCE` (default `2026-10-20`, the deployment day) is the earliest call date the worker will look at, so deploying analyses only calls from that day on (Central); earlier days are only ever done by the explicit backfill. The worker never looks back more than 3 days from today.

## October 2026 backfill

Preview first (no model request, no write):

```
python -m ops.backfill_priority_cases --from 2026-10-01 --to 2026-10-20
```

It prints how many calls lack a transcript (never transcribed), how many are already up to date, how many would be sent to the model (all of them; the keyword split is diagnostic only), plus a rough token estimate (labelled as an estimate).

Run it:

```
python -m ops.backfill_priority_cases --from 2026-10-01 --to 2026-10-20 --apply
```

Options: `--to` defaults to today (Central); `--limit` (default 5000 calls per run); `--batch-size` (25); `--rpm` (default 20 requests/minute from this process, on top of F3's 429 back-off — it is a separate process, so it has its own limiter). Run it with the production environment (the same variables as the backend) from the backend directory.

It is restartable: stop it any time and re-run; calls already analysed for their current transcript and prompt version are skipped. When the provider slows down or credits run out it stops and says so. At the end it prints run totals, the state of the range, and the **measured** token usage reported by the provider (no cost is invented; multiply by your plan's rates). Per-call usage is also stored in `call_priority_analysis.input_tokens/output_tokens`.

Monitoring queries (read-only):

```sql
SELECT analysis_status, analysis_source, count(*), sum(input_tokens) AS tin, sum(output_tokens) AS tout
FROM call_priority_analysis GROUP BY 1, 2;
SELECT call_id, attempts, error, last_attempt_at FROM call_priority_analysis WHERE analysis_status = 'failed';
```

## Tests

* Backend: `Tests/test_priority_cases.py` (detection variants, validation, retries, RBAC), `Tests/test_priority_cases_worker.py` (job wiring), `Tests/test_priority_cases_db.py` (claim/idempotency/retries/failure isolation/date boundaries/duplicates/HTTP access; needs `PRIORITY_CASES_TEST_DATABASE_URL` pointing at a **local** disposable PostgreSQL, otherwise skipped).
* Frontend: `node --test src/data/priorityCases.test.js` (columns, sections, filters, serials, copy, selection, export, access).

## Known limits

* The Excel export uses the app's existing SpreadsheetML writer, so the file is `Priority_Cases_YYYY-MM-DD.xls` (like Calls / Case Types + EQ exports), not `.xlsx`.
* One transcript is capped at 14 000 characters for the model (like the other AI features); evidence beyond that is not seen.
* Transcripts longer than `MAX_TRANSCRIPT_CHARS` (14,000) are truncated before the request, so a form discussed only in the tail of a very long call can be missed.
* Sending every transcript raises model usage compared with the earlier keyword gate; the worker is bounded (`PRIORITY_CASES_BATCH_LIMIT` per minute) and results are cached per transcript hash + prompt version.

## Contextual prompt and existing results

Migration `075_priority_prompt_contextual` (run by the release command `python -m migrations.runner`) makes the contextual wording from `services/priority_case_prompt.py` the active `fd_prompts` version, but only when the active version is still the untouched seed (`updated_by = 'seed'`); an administrator's edit is never overwritten, and a rerun is a no-op. It analyses nothing.

**Existing results are never re-analysed automatically.** The worker runs `run_batch(reanalyse=False)`: it only picks up calls with no result yet, pending/failed-and-due calls, or a changed transcript. A new prompt version or the `RESULT_REV` bump does NOT re-queue calls that already have a completed result (so October calls already analysed keep their rows, including the ones the old keyword gate stored as "nothing found"). Only the explicit backfill (`reanalyse=True`) re-analyses them.

Backfill (not run yet): `python -m ops.backfill_priority_cases --from 2026-10-01 --to 2026-10-20` (preview), then `--apply`.
* Row data is read from the stored result; an H4 and an H4EAD call are judged independently by the prompt (an H-4 work-permit-only call is H4EAD, not H4). Change the prompt if the business wants H4EAD calls to count as H4 too.
