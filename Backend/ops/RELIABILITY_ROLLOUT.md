# Reliability changes — local preparation only

No production deployment, migration, process termination, or throttling is authorized in this session.

## Preserved evidence

Snapshots are in `../verification/incident-2026-09-23/`. Capture times are UTC.
`pg_stat_statements_info.stats_reset` is **2026-09-18 13:01:26.557338+00**;
the supplied query totals are not a 24-hour incident-only sample. PostgreSQL
started **2026-09-18 13:10:45.540892+00** and reports timezone UTC.
The first two snapshots (08:08–08:19 UTC September 23) show no increases in
the top 40 statement call counts and no sampled blocking sessions. These
snapshots cannot rule out activity between samples or failures not represented
in completed-statement counters. The public Railway health endpoint returned
`status: ok`, but the current deployment exposes no commit identifier.

The database also reports 111 cumulative deadlocks and substantial cumulative
temporary-file writes; neither is attributable to last night without logs.
The inspected completed statements with temporary writes were predominantly
observability/catalog queries, including our first statistics collector. The
collector now uses `pg_stat_statements(false)` to avoid materializing long SQL
texts. Do not poll the full text-bearing statistics view at high frequency.
Two connection attempts in the third capture failed; the artifact records
them explicitly rather than treating missing data as zero.

Still unavailable: timestamped Railway deployments/restarts, database and
gateway historical error logs, dashboard timezone, network series/unit/export,
and an inventory of every production process. No route was throttled because
no active broad-query producer was established.

## What changed

- Analysis filters, canonical-call ranking, counts and pagination execute in
  PostgreSQL. The existing Hannah AI session matcher operates on six narrow,
  date-scoped columns; it never fetches transcripts or analysis JSON.
- Leads date/owner/search filters and pagination execute before enrichment.
  Legacy full-range frontend consumers fetch sequential pages of 200 records
  and only display the completed result, preserving aggregate correctness.
- Analysis and Leads omit transcript/raw JSON payloads. Authenticated call
  content requests fetch those on demand. FlintDesk transcript buttons use
  that endpoint. Unpaginated Analysis consumers remain date-bounded for
  compatibility with dashboard calculations.
- Dashboard duration is a single SQL aggregate, preserving assignment history
  and voicemail rules. Other dashboard metrics retain their existing sources.
- Startup does not run DDL, even if RUN_STARTUP_MIGRATIONS is set. Startup logs
  include Railway revision/deployment/replica identifiers; health returns the
  revision when Railway supplies it.

## Release procedure for a later, separately authorized deployment

1. Review diffs and run the local checks. Deploy backend before frontend so the
   new content endpoint exists when transcript buttons start using it.
2. Verify the existing schema and legacy migration/data state before adopting
   the ledger. Do **not** replay all historical data migrations on production.
   The manifest intentionally retains the former startup selection and omits
   previously removed seeds and destructive migration 050. There are no new
   business-table schema changes in this patch.
3. Only for an already verified installation, initialize the ledger with
   `python -m migrations.runner --baseline`. This records the manifest without
   replaying it. Never baseline an unverified or incomplete schema.
4. Configure a release job to run `python -m migrations.runner` once per release,
   not once per web replica. Use PostgreSQL 17+ (the current production version).
   It acquires a transaction advisory lock and writes the ledger atomically
   with migration changes. Lock timeout: 2 seconds; statement timeout: 30
   seconds; total transaction timeout: 120 seconds. Failure aborts the release.
   Long backfills/concurrent index builds require their own reviewed job.
5. Verify each replica/worker's deployment SHA in Railway logs and health
   responses, and exercise Leads paging, agent/date filters, transcript opening,
   Analysis totals and Dashboard duration. Observe interval query statistics,
   request latency, pool checkout waits and lock waits during the rollout.

## Read-only capture

`python ops/capture_incident.py --output <private-artifact-path>.json`

This loads the existing DATABASE_URL, never imports the app or starts its
scheduler, and runs read-only transactions with bounded statement/lock waits.
It does not reset counters. Retain artifacts privately and keep their reset
timestamps alongside any interval deltas.

## Local verification (September 23)

- 11 new PostgreSQL integration tests pass: filters-before-pagination, legacy
  and invalid dates, narrow payloads and detail loading, duration aggregates,
  assignment history/manual names, duplicate-call selection, lead ownership,
  proposed-call filters, migration exactly-once behavior, rollback and lock
  timeout. The tests use disposable localhost schemas, never production.
- Full backend suite with local analysis-job integration enabled: 143 passed,
  one pre-existing failure in `Tests/test_cors.py:57`. That assertion expects
  only AutoFlint's origin; HEAD already includes both AutoFlint and FlintDesk.
  The final additional serializer/detail test also passed in the 11-test run.
- FlintDesk production build passed; all 14 frontend data tests passed.
  Lint completed with existing warnings; diff whitespace checks passed.
- No production deployment or migration was performed. Live historical error
  correlation and production replica/build verification remain outstanding.
