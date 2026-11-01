# Assigning the Calls, Follow Ups and the Calls-page filters (2026-10-25 changes)

Everything here is local until it is committed, merged and deployed. Migrations are NOT run by a deploy: run
`python -m migrations.runner` first (075 and 076 are pending on prod before this; 077 is new).

## Migrations
| # | What | Rewrites data? |
|---|------|----------------|
| 076 | `fd_case_lifecycle_stage_of` knows "Wrong Number Dialed" (Dropped stage) | No |
| 077 | `fd_follow_up_retainer` (one row per Success PC call: the eight Team Lead Fills values + assignment state) | No |

Rollback of 077: `DROP TABLE fd_follow_up_retainer;` (nothing else depends on it; Call Updates treats a missing table as zero).

## Follow Ups (`services/follow_ups.py`, `routes/flintdesk/follow_ups.py`, prefix `/flintdesk/follow-ups`)
* `GET ?start_date&end_date&agent` - canonical Calls-page records with Case Status Change = Success PC. Admin / Team Lead / SME get all
  (optionally one agent); an agent gets only calls **submitted to them** as Retainer Owner. `can_edit` says which.
* `PATCH /{call_id}` - saves only the fields sent. Admin / Team Lead / SME only (403 otherwise). Values are validated
  (Business Status: PC Only | PC + Retainer; Follow Up Required: Yes | No; Action Status: Pending | Completed | Dropped;
  Retainer Status: Success | Lost; Paid Amount >= 0 with two decimals; Retainer Owner an active user). A blank clears a field.
  Only Success PC calls accept data (422 otherwise). Giving a submitted call to a different owner withdraws the submission.
* `POST /{call_id}/submit` - needs a Retainer Owner; stamps `assigned_at`/`submitted_at`/`submitted_by`; notifies the owner once
  (`follow_up_assignment`, opens Assigning the Calls > Follow Ups). Submitting again does nothing.
* **Follow Up Call** label / green row: a submitted call whose owner phoned the client (last 10 digits of the number) from one of the
  owner's extensions (`fd_assignments`) on the same America/Chicago calendar day as the submission.
* **Retainer Verification** has no specified values; it is stored as free text.
* Call Updates shows, below the total, Retainer Follow Up (Follow Up Required = Yes), Retainer Lost and Success Retainer, per Retainer Owner
  by the call's date, from submitted rows only.

## Calls page filters (`services/analysis_read_query.py`, `routes/calls.py`)
Applied by the Apply button together with the other filters. New/extended query parameters on `/calls/analysis`, `/calls/` and
`/calls/analysis/follow-up-dates`:
* `lifecycle_stage=open` is the status-based Open Cases list (blank, Open, Blocker, Same Client Context, Client Side Delay, Dropped).
* `aging_dependency=agent|other_agent` (with the Agent filter) - the saved AI answer; no model call.
* `priority_form=RFE|I-130|I-485|H4|H4-EAD` - a completed priority analysis that confirmed the form on the call or any duplicate leg
  (identical to the removed Priority Cases page). Admin / Team Lead / SME only (403).
* `action_status=Completed|Pending`, `review_status=pending|completed` (Log Updates: the green Completed button), `case_status`.

## Other behaviour
* Leads are ordered by the number in the Lead ID, newest first, then the ID text (deterministic across pages).
* Call Updates: agent order Shrishti, Priyanka, Gokul, Gracy, Christobel, Edgin, then the rest; per-value counts under each lifecycle category.
* Case Status Change is selectable on any call (no audio/transcript/answer needed). The old outbound NO ANSWER block was removed in the UI and the API.
* Default date on every date-filtered page: the previous working day in Central Time (Mon/Sat/Sun -> Friday). No holiday calendar exists.

## Release sequence (nothing below has been run against production)
Order matters. Do not skip a step and do not reorder.

**Before anything**
1. Production today = tag `gcp-backend-prod-58f429c-fix1` (`7918cb0`), migrations applied through 074, Priority prompt v1 (seed) active.
2. Merge the backend branch into `main` (it already contains the Cloud Run `Query` fix `900efdc`, which is only on the deployed tag). Create the new backend tag on the merged commit. The F2 branch is NOT part of this release.

**Step 1 - migrations (production database, once, by a person with access)**
`python -m migrations.runner` from the merged backend checkout. It applies 075, 076 and 077 in that order in ONE transaction (all or nothing,
2 s lock wait, 30 s per statement, advisory lock so a second run cannot overlap). Expected output: `Applied migrations.075_...`, `076_...`, `077_...`.
Run it while no one is saving calls: 077 adds foreign keys to `calls` and `fd_users`, which needs a brief lock (it fails safely and can be re-run).
Verify:
- `SELECT version FROM public.app_schema_migrations WHERE version LIKE 'migrations.07%' ORDER BY version;` lists 075, 076, 077
- `SELECT fd_case_lifecycle_stage_of('Wrong Number Dialed');` returns `dropped`
- `SELECT version, is_active, updated_by FROM fd_prompts WHERE prompt_key='priority_case_classification' ORDER BY version;` shows v2 active (`migration:075`)
- `SELECT to_regclass('fd_follow_up_retainer');` returns the table name

**Step 2 - backend** (only after step 1 succeeded): paste the new tag into the GCP trigger and deploy. Smoke test: sign in, open Calls, Dashboard, Assigning the Calls > Follow Ups, Settings > Prompt Management.
Doing this BEFORE 076 makes every Wrong Number Dialed save permanently stage `open` (migrations never rewrite rows).

**Step 3 - frontend** (only after step 2 is live). A new frontend on the old backend would show unfiltered lists and offer a status the server rejects.

**Not part of this release / not to be done:** the October Priority backfill, the F2 transcription fix, any `.env` or service configuration change, 2FA.

**Rollback**
- Frontend: redeploy the previous frontend first.
- Backend: redeploy `gcp-backend-prod-58f429c-fix1`. Migrations 076 and 077 are additive and harmless to the old code. 075 left prompt v2 active; the old code was not tested against it, so
  to return to the seed prompt run `UPDATE fd_prompts SET is_active = (version = 1) WHERE prompt_key = 'priority_case_classification';`
- 077 can be removed with `DROP TABLE fd_follow_up_retainer;` (Call Updates treats a missing table as zero).
- Database: the PostgreSQL version must be 16 or newer (the runner now only sets `transaction_timeout` on 17+). Production has run the runner before.
