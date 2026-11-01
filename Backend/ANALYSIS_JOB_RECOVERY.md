# AI request recovery

Both scheduled F3 processing and the registered FlintDesk Analyse endpoint use
`services/analysis_jobs.py`. Admission is serialized by a short database row
lock per call. Autoflint and Flintdesk have separate job records. No database
connection is held while waiting for AI, and waiting batches are not leased.

The Autoflint eligibility check also normalizes completed IDs to strings. The
production database column is UUID while its ORM declaration is Text; psycopg2
can return UUID objects despite that declaration. Previously the string-versus-
UUID comparison could requeue already-completed calls on later daily runs.

## Rollout

Migration `046_analysis_jobs` is registered in backend startup. Stop old backend
workers and standalone backfill processes before starting the updated version:
old code does not honor the new guard. If startup migrations are disabled, run
the migration before enabling workers. If the jobs table is unavailable, the
new pipeline fails before making a model request. This change does not repair
historical completion flags or refund previous requests.

The migration enables row-level security with no direct client policies. The
backend must connect as the table owner or a role that can bypass RLS, as with
the existing service database connection.

## States

- `generating`: one worker has permission to send the request. Other workers
  cannot resend it, even if that worker crashes. Old generating jobs require
  investigation; admission changes jobs older than 15 minutes to review state.
- `response_ready`: the full provider response (including model and usage when
  provided) is committed. Parsing/application-save failures retain this response.
  The next eligible processing attempt applies it without another AI request.
- `completed`: application updates and job completion committed together.
- `review_required`: the outcome was uncertain, or generation failed. Automatic
  processing stops for this job. Review the provider request/billing logs and
  server logs before authorizing any replacement request. Do not delete/reset
  these jobs simply to clear the queue.

Read-only inspection:

```sql
SELECT id, call_id, application, status, created_at, updated_at, error,
       response->>'model' AS model, response->'usage' AS usage
FROM analysis_jobs
WHERE status <> 'completed'
ORDER BY created_at;
```

A saved response can be replayed by the normal F3/manual Analyse path. Scheduled
recovery retains existing date/phone scopes; older out-of-scope calls can use the
existing scoped backfill tool. Jobs requiring review are excluded from scheduler
batches so they do not block other calls. The initial checkpoint retries database
writes three times, never the model. If all checkpoint writes fail or the process
dies before saving the response, recovery may require provider investigation;
the guard deliberately prevents a blind paid retry.

Manual Analyse reuses completed work. Explicit Re-analyse uses
`?reanalyse=true&request_id=<UUID>`. The UI retains the ID across errors while the
page remains open, preventing repeat charges for retries of the same action.
After success, another explicit Re-analyse creates a new paid job. An active job
cannot be bypassed by supplying a different request ID.

## Tests

Set `ANALYSIS_TEST_DATABASE_URL` to a disposable **localhost** PostgreSQL database
and run `python -m pytest Tests/test_analysis_jobs.py -q`. Tests use random schemas
and synthetic AI responses; no provider calls or production database writes occur.
