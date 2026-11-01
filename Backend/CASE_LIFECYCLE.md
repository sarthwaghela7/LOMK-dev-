# Case Lifecycle

Five lifecycle stages, one per **call record** (a `call_analysis_results` row). Client number, context, lead and agent never
group or de-duplicate calls: five calls of one client are five counts.

| Stage | Case Status Change values |
|---|---|
| Open | Open, Blocker, Same Client Context, blank/NULL |
| In-Transition | Initial email sent (Fee Details shared), Initial Email Follow Up, Fee details shared & FU, PC link shared, PC Follow Up |
| Dropped | Client Side Delay, Dropped |
| Success | Success PC, Success GE |
| Lost | automatic only: `<last In-Transition status> & Lost` |

Precedence: Lost suffix → Success → Dropped → In-Transition → Open. A blank status never erases an established
Lost / Dropped / Success outcome.

## Where it lives
- `services/case_lifecycle.py` – the rules (Python) and the dropdown list; `fd_case_lifecycle_stage_of()` in migration 073 is the SQL twin (a test asserts they agree).
- `migrations/073_case_lifecycle.py` – columns, trigger, history table, outbox tables, indexes. No data rewrite.
- A **trigger** on `call_analysis_results` keeps the stage right for every writer of `fd_m_case_status_change`
  (Calls page, the existing related-call closure confirmation and its revert, legacy routes, imports). The existing save and
  propagation code is therefore unchanged: whatever status each call ends up with decides its own stage.
- `ops/backfill_case_lifecycle.py` – categorises existing calls (dry run by default).
- `services/case_lost_processor.py` – Lost processing, Teams batches, delivery. Scheduled in `scheduler/jobs.py`.

## Columns (call_analysis_results)
`case_lifecycle_stage`, `in_transition_entered_at`, `last_in_transition_status`, `lost_at`, `case_lifecycle_updated_at`.
History: `fd_case_lifecycle_events` (every stage change) and the existing `fd_case_status_history` (the automatic Lost write, `changed_by` NULL).

## In-Transition timer
Starts when a call enters In-Transition; **kept** when it moves between In-Transition statuses, on edits, reads and re-saves, and is
never touched by another call. If it leaves In-Transition and later re-enters, the timer restarts at re-entry.

## 30-day rule and timezone
Business timezone `America/Chicago`. A call is Lost-eligible when `today(Central) − entry date(Central) ≥ 32` calendar days
(`LOST_AFTER_DAYS`). Entered 20 Oct → still In-Transition through 19 Nov (30 days), 20 Nov is the boundary day (not yet Lost),
21 Nov → Lost when the processor runs that day. Tested with exactly that example.

## Lost processor
One conditional `UPDATE … FROM (SELECT … FOR UPDATE SKIP LOCKED)` per chunk: concurrent workers/instances never take the same call;
a status saved first (Dropped, Success, anything else) is seen by the UPDATE's own WHERE and wins. The transition row is inserted in the
same transaction. Re-running changes nothing (`UNIQUE (analysis_id, in_transition_entered_at)`).
Runs hourly at :20 Central **only when `CASE_LOST_PROCESSOR_ENABLED=true`** (default off, so deploying does not mass-move calls).
It is state-driven, so a restart or missed run only delays it.

Check what would move before enabling: `SELECT count(*) FROM call_analysis_results WHERE case_lifecycle_stage='in_transition' AND in_transition_entered_at < <cutoff>` or call `case_lost_processor.count_eligible(db)`.

## Teams notifications (not active until you connect a workflow)
Every **10 newly committed Lost transitions** become one row in `fd_case_lost_batches` (`pending` = `delivered_at IS NULL`).
Event schema `flintdesk.lost_cases.batch.v1`:
```json
{"schema":"flintdesk.lost_cases.batch.v1","batch_id":"<uuid>","idempotency_key":"<same uuid>","sequence":1,"count":10,
 "created_at":"…","calls":[{"call_id":"<uuid>","call_reference":"AF#123","previous_status":"PC link shared","lost_status":"PC link shared & Lost","lost_at":"…"}]}
```
No client names, numbers or call content. Delivery: set `LOST_CASE_TEAMS_WEBHOOK_URL` (a Teams Workflows "When a Teams webhook request is received"
URL) and the scheduler posts due batches every 2 minutes as an Adaptive Card message (the raw event is in the `flintdesk` field), header
`Idempotency-Key: <batch_id>`. Failures retry with exponential backoff (2 min doubling, cap 6 h). Delivery is at-least-once; the batch id lets the
workflow ignore a repeat. Without the URL nothing is sent and batches wait in the table (you may poll it instead).

## Calls page / Dashboard
- `GET /calls/analysis`, `/calls/`, `/calls/analysis/follow-up-dates` accept `lifecycle_stage=open|in_transition|lost|dropped|success` (single choice, validated).
  Applied in SQL together with every other filter, pagination and totals. Export uses the already filtered rows.
- `GET /api/dashboard/case-lifecycle?start_date&end_date&agent&number&extension` → `{counts:{…}, total}`; one SQL aggregation over the Calls-page population
  (same filters as the Open Call Aging cards). `open+in_transition+lost+dropped+success == total`. A call with no analysis yet counts as Open.
- Open Call Aging endpoint, formulas and values are untouched; the Dashboard calculator receives statuses in its original spelling (`for_metric_calculator`).
- Dashboard **Call Log Status** table: one column per dropdown value (same 12, same order); statuses still stored in an old spelling count in the column of their new wording; a `… & Lost` status is not a dropdown value and is reported in the table's "status not in the list" note.

## Dropdown change and old values
The backend accepts the 12 dropdown values; a stale tab's legacy spelling is accepted and stored under the new wording; a `… & Lost` value is accepted only unchanged.
Legacy → new (proposed, **needs your confirmation before any data rewrite**): Success - PC→Success PC, Success - GE→Success GE,
initial email followup→Initial Email Follow Up, Initial email sent.→Initial email sent (Fee Details shared); Fee details shared & FU is now a dropdown value of its own (unchanged, In-Transition),
PC shared and lost→PC link shared & Lost (stage Lost), Client Side delay / Same client context → new casing, **Move to next date → undecided** (classified Open, reported by the dry run).
The classifier already understands both spellings, so the backfill below does not depend on the rewrite.

## Release sequence (nothing here has been run against production)
1. Run the migration BEFORE merging the backend: `python -m migrations.runner` (adds 073; additive, fast).
2. Dry run: `python -m ops.backfill_case_lifecycle` → review counts, timer sources, undecided values.
3. `python -m ops.backfill_case_lifecycle --apply` (batches, rerun-safe, only touches the five lifecycle columns).
4. Verify: `SELECT count(*) FROM call_analysis_results WHERE case_lifecycle_stage IS NULL` = 0.
5. Merge/deploy backend and frontend.
6. When ready: set `CASE_LOST_PROCESSOR_ENABLED=true`; later set `LOST_CASE_TEAMS_WEBHOOK_URL`.

Backfill timer policy: history (`fd_case_status_history`) → `fd_m_date_entry` → migration moment. `calldate` is never used.

## Rollback
Backend: redeploy the previous version (the extra columns/trigger are harmless to it). To remove the feature from the database:
`DROP TRIGGER fd_case_lifecycle ON call_analysis_results;` then drop the five columns, `fd_case_lifecycle_events`, `fd_case_lost_transitions`, `fd_case_lost_batches` and the two functions.
Set `CASE_LOST_PROCESSOR_ENABLED=false` to stop automatic Lost at any time. Statuses already moved to `… & Lost` can be reverted from `fd_case_status_history`.

## Local demo
`ops/local_lifecycle_demo.py` builds a disposable local database (refuses non-local hosts) with seeded calls in all five stages.
