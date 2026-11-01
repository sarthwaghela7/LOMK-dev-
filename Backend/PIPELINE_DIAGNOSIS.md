# Pipeline Diagnosis

This document is a read-only investigation of the AutoFlint processing pipeline. No business logic was changed.

## Pipeline Flow

### 1. F1 sync
Primary implementation: [services/Database_sync/f1.py](services/Database_sync/f1.py)

Execution flow:
1. Resolve the run date in US/Central time. If no date is provided, default to yesterday.
2. Fetch Vitel call metadata for that date via the Vitel API.
3. Parse XML records into call dictionaries.
4. Filter calls by allowed destination/extension rules.
5. Load existing call keys for the batch dates.
6. Insert only non-duplicate calls into `calls`.
7. Load audio files from the S3 date prefix.
8. Parse audio filenames and match audio to DB calls.
9. Update matched calls with S3 audio paths.
10. Commit the batch.
11. Write a sync log in `sync_logs`.

Important note: there is also a duplicate/stale F1 implementation in [f!.py](f!.py). It is not the active module invoked by `python -m services.Database_sync.f2`, but it contains the same class of status inconsistencies and should be treated as a maintenance risk.

### 2. F2 processing
Primary implementation: [services/Database_sync/f2.py](services/Database_sync/f2.py)

Execution flow:
1. Resolve the run date, but `fetch_unprocessed_records()` does not actually use it.
2. Query `calls` for rows where `processing_status == "not processed"` and `audio_s3_key IS NOT NULL`.
3. Keep only rows from the last 48 hours.
4. For each record, call the pipeline with `audio_s3_key`.
5. Pipeline runs STT, then LLM analysis, then call-quality scoring.
6. Update the `calls` row with transcript and `processing_status = "processed"`.
7. Create and save a `call_analysis_results` row.
8. On exception, roll back and mark the call as `not processed` with `error_message`.

### 3. Pipeline internals
Primary implementation: [services/Processing_pipeline/pipeline.py](services/Processing_pipeline/pipeline.py)

Flow:
1. `STT.transcribe()` downloads audio from S3 and submits it to the STT API.
2. `LLM.generate()` sends the transcript to the LLM API.
3. `CallQuality.get_quality()` sends the same transcript to the quality prompt.
4. The three results are returned to F2.

### 4. Storage and API support
Relevant files:
- [storage/models.py](storage/models.py)
- [storage/s3.py](storage/s3.py)
- [routes/calls.py](routes/calls.py)
- [routes/auth.py](routes/auth.py)
- [utils/audit_log.py](utils/audit_log.py)
- [Tests/clear_tables.py](Tests/clear_tables.py)
- [Tests/schema_1.py](Tests/schema_1.py)
- [check_status.py](check_status.py)
- [migrate_status.py](migrate_status.py)

## SQL Queries

### Pipeline-related ORM queries

#### F1
1. Load existing call keys:

```sql
SELECT call_date, call_time, src, dst, billsec
FROM calls
WHERE call_date IN (:dates)
```

2. Insert into `calls` via ORM flush/commit.
3. Insert into `sync_logs` via ORM commit.

#### F2
1. Fetch candidate records:

```sql
SELECT *
FROM calls
WHERE processing_status = 'not processed'
  AND audio_s3_key IS NOT NULL
ORDER BY call_date ASC, call_time ASC
```

2. Update `calls` row with transcript, status, error message, and timestamps.
3. Insert into `call_analysis_results` via ORM commit.

#### API and maintenance SQL

1. Login query in [routes/auth.py](routes/auth.py):

```sql
SELECT id, email, pass_hash, role
FROM users
WHERE email = :email
```

2. Calls list endpoint in [routes/calls.py](routes/calls.py):

```sql
SELECT *
FROM calls
WHERE call_date BETWEEN :start_date AND :end_date
ORDER BY call_date DESC, call_time DESC
```

3. Analysis list endpoint in [routes/calls.py](routes/calls.py):

```sql
SELECT *
FROM call_analysis_results
WHERE call_recording_date BETWEEN :start_date AND :end_date
ORDER BY call_recording_date DESC
```

4. Audit log insert in [utils/audit_log.py](utils/audit_log.py):

```sql
INSERT INTO system_audit_logs
    (id, user_id, action, status, message, module_name, meta_data, organization_id, created_at)
VALUES
    (:id, :user_id, :action, :status, :message, :module_name, :metadata, :organization_id, :created_at)
```

5. Table clear script in [Tests/clear_tables.py](Tests/clear_tables.py):

```sql
TRUNCATE TABLE public.calls, public.sync_logs RESTART IDENTITY CASCADE
```

6. Schema inspection script in [Tests/schema_1.py](Tests/schema_1.py):

```sql
SELECT table_schema, table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = ANY(:table_names)
ORDER BY table_schema, table_name, ordinal_position
```

7. Status inventory in [check_status.py](check_status.py):

```sql
SELECT processing_status, COUNT(call_id)
FROM calls
GROUP BY processing_status
```

8. Status migration logic in [migrate_status.py](migrate_status.py): multiple `SELECT COUNT(*)`, `SELECT *` by status, and `UPDATE calls SET processing_status = ...` via ORM.

## Filters

### F1 filters
- Keeps only outgoing calls from extensions `101` and `102`.
- Keeps only incoming calls whose destination is one of `3128781255`, `13128781255`, `+13128781255`, `vmu3128781255`, or `601`.
- Drops all other Vitel records before insertion.
- Removes duplicate rows by `(call_date, call_time, src, dst, billsec)`.
- Only queries existing DB rows for the dates present in the current batch.
- Only matches audio if destination, source, date, and time tolerance all pass.
- Only processes `.wav` objects from the S3 date folder.
- Skips S3 folder placeholders.
- Skips audio filenames with fewer than 6 dash-delimited parts.

### F2 filters
- Requires `processing_status == "not processed"`.
- Requires `audio_s3_key IS NOT NULL`.
- Requires the record to fall within the last 48 hours.

### API filters
- `routes/calls.py` filters `calls` by `call_date BETWEEN start_date AND end_date`.
- `routes/calls.py` filters `call_analysis_results` by `call_recording_date BETWEEN start_date AND end_date` using string comparisons.

## Date Filtering

Observed date rules:
- F1 defaults to yesterday in US/Central when no date is passed.
- F1 requests Vitel data with `startdate = enddate = run_date`.
- F1 loads duplicate keys only for the distinct `call_date` values in the current batch.
- F1 loads S3 audio from the matching `YYYY/MM/DD` prefix.
- F1 matches audio only when the parsed audio `call_date` equals the DB `call_date`.
- F2 defaults to yesterday in US/Central in `_resolve_date()`, but that value is not used by `fetch_unprocessed_records()`.
- F2 effectively uses a rolling 48-hour cutoff instead of the requested date.
- `routes/calls.py` uses explicit `start_date` / `end_date` query parameters.

## Timezone Conversions

Observed conversions and timezone-dependent code:
- F1 `_utcnow()` returns naive UTC.
- F1 run date calculation uses US/Central.
- F1 S3 batch selection uses US/Central.
- F1 converts S3 `LastModified` timestamps to US/Central.
- F1 sync logging uses US/Central timestamps.
- F2 computes `now_central` in US/Central.
- F2 compares records against a `cutoff` derived from US/Central.
- F2 builds `record_dt` with `datetime.combine(..., tzinfo=central)`.
- F2 writes `updated_at` using UTC.
- `utils/audit_log.py` uses naive `datetime.utcnow()`.

## Processing Status Values

Values observed in the workspace:
- `not processed`
- `not_processed`
- `processed`
- `audio_mapped`
- `pending`
- `failed`

Where they appear:
- F1 inserts `not processed`.
- F1 later updates matched rows to `not_processed` in the active implementation.
- F2 looks only for `not processed`.
- F2 writes `processed` on success.
- F2 rewrites failed rows back to `not processed`.
- `migrate_status.py` expects to convert `pending`, `failed`, and `not_processed` into `not processed`.
- The status inventory script confirms the database currently contains both `not_processed` and `not processed`.

## Where Records Can Be Skipped

### F1 skip points
- Non-qualifying calls are dropped by the outgoing/incoming allowlist.
- Duplicate calls are skipped after key comparison.
- Rows already present in the DB are skipped.
- Invalid S3 objects are skipped if they are folders or non-WAV files.
- Invalid audio filenames are skipped because `_parse_audio_filename()` returns `None`.
- Audio candidates are skipped when source, destination, date, or time tolerance do not match.
- S3 fetch failure aborts the batch before inserts are committed.

### F2 skip points
- Any row with status `not_processed` is invisible to the query.
- Any row with status `not processed` but no `audio_s3_key` is invisible.
- Any row older than 48 hours is excluded after the in-memory cutoff check.
- Any `process_record()` exception ends that record’s analysis path and moves on to the next record.

## Exceptions Being Swallowed

Observed swallow/containment points:
- `F2Processor.process_record()` catches `Exception`, logs it, rolls back, marks the row as `not processed`, and does not re-raise.
- The nested DB-update failure handler in `process_record()` catches and suppresses the second exception after rollback.
- `_parse_audio_filename()` returns `None` on any parsing failure and only logs the error.
- `_log_sync()` catches and suppresses sync-log insert failures after rollback.
- `utils/audit_log.py` catches all exceptions and prints the error instead of raising.

## Database Updates

### Calls table
- F1 inserts new rows.
- F1 updates matched rows with audio path fields and status.
- F2 updates `transcript`, `processing_status`, `updated_at`, and potentially `error_message`.

### Call analysis table
- F2 inserts one `call_analysis_results` row per successful record.

### Sync logs
- F1 inserts one `sync_logs` row per sync run.

### Audit logs
- `utils/audit_log.py` inserts one `system_audit_logs` row per auth event.

## Transactions

Observed transaction boundaries:
- F1 uses one session for inserts, audio-path updates, and the final commit.
- F1 flushes before the final commit so inserted rows get IDs before matching.
- F1 writes sync logs in a separate session and commits them independently.
- F2 flushes after updating the call row.
- F2 commits after the analysis record is added.
- F2 rolls back the main session on any processing error.
- F2 starts a second transaction inside the exception path to mark the call as `not processed` and commit that status.
- `Tests/clear_tables.py` uses `engine.begin()` for a single explicit transaction around TRUNCATE.
- `utils/audit_log.py` opens a connection, executes an insert, and commits directly.

## Root Causes

### 1. Status mismatch between F1 and F2
This is the clearest cause of the observed `No unprocessed records found` symptom.

- F1 writes both `not processed` and `not_processed` in different parts of the same pipeline implementation.
- F2 only queries `not processed`.
- The live database already contains both values, so half the records are invisible to F2.

Evidence from the database status check showed both values present:
- `not_processed`: 15 rows
- `not processed`: 15 rows

### 2. F2 ignores the requested date and uses a rolling 48-hour window
`fetch_unprocessed_records()` accepts `target_date` but does not use it.
This means:
- A record can exist for the desired date and still be skipped if it falls outside the last 48 hours.
- The CLI/date parameter gives a false impression of date-scoped processing.

### 3. F2 timezone construction is fragile
`datetime.combine(record.call_date, record.call_time, tzinfo=central)` assigns a `pytz` timezone directly instead of localizing the naive datetime.
That can produce incorrect comparisons or offsets in timezone-aware logic.

### 4. STT upload path and file-type handling are inconsistent
`STT.transcribe()` always downloads to `storage/audio/temp.mp3` and then infers content type from that local extension.
If the source audio is WAV, the code still uploads it as `temp.mp3` with `audio/mpeg`, even though the bytes are not MP3.

This is a plausible cause of:
- corrupted-looking downloaded audio files
- HTTP 400 responses from the STT API for some recordings

### 5. The LLM result is treated as a JSON object, but the client returns a string
`LLM.generate()` returns `response.json()["choices"][0]["message"]["content"].strip()`, which is a string.
F2 then does:
- `raw_llm_json = llm_result if isinstance(llm_result, dict) else {}`

As a result:
- `raw_llm_json` is almost always `{}`
- the 39 expected fields are not parsed from the model output
- the analysis row is saved without the actual structured JSON payload

This is the most direct reason the analysis JSON is not being persisted into `call_analysis_results`.

### 6. Analysis persistence depends on successful end-to-end completion
If STT or LLM fails, F2 rolls back and marks the record back to `not processed`.
That means any partial progress is lost by design.
This is not inherently wrong, but it magnifies the impact of the upstream failures and makes the record appear unprocessed again.

### 7. F1/F2 status names were not normalized at the model level
There is no constraint or canonical enum on `Call.processing_status`.
That allowed multiple spellings to persist and diverge operationally.

## Suspected Bugs

- F1 updates audio-mapped rows to `not_processed` while F2 queries `not processed`.
- F2’s `target_date` argument is effectively ignored.
- F2’s 48-hour cutoff can silently exclude relevant rows.
- F2 localizes timestamps incorrectly with `tzinfo=central`.
- STT sends a potentially mismatched filename/content-type pair to the API.
- F2 expects a dict-shaped LLM response but receives a string.
- `call_analysis_results.raw_llm_json` is filled with `{}` instead of the actual model output.
- `call_analysis_results.processing_status` is never populated.
- `error_message` is not present on `CallAnalysisResult`, so analysis failures cannot be stored there.

## Missing Logging

- F2 does not log the exact number of rows excluded by status mismatch vs. 48-hour cutoff vs. missing audio key.
- F2 does not log the parsed content type or source file extension mismatch before STT upload.
- F2 does not log the raw LLM response body before assuming it is a dict.
- F2 does not log whether `call_analysis_results` received a full structured payload or only defaults.
- F1 does not log how many records were excluded by each allowlist rule.
- F1 does not log audio filename parse failures with enough context to trace broken recordings back to the source object.

## Missing Validation

- No validation that `processing_status` only uses one canonical value set.
- No validation that downloaded audio bytes match the expected file extension.
- No validation that STT responses are valid for the specific file type before posting.
- No validation that LLM output is parseable JSON before trying to persist it as structured data.
- No validation that `CallAnalysisResult` receives the expected 39+1 fields.
- No validation that the requested F2 date actually scopes the query.

## Silent Failures

- Records can disappear from F2 visibility purely because the status spelling differs.
- Invalid S3 filenames are skipped silently after a warning.
- Non-matching audio files are excluded without a persisted explanation.
- Analysis payloads are effectively discarded when the LLM returns a string.
- Auth audit logging errors are swallowed.
- Sync log insert failures are swallowed.

## Bottom Line

The dominant root cause for the current pipeline failure is the status split between `not processed` and `not_processed`, combined with F2 querying only one of those values. The analysis persistence failure is a separate but related problem: F2 assumes structured JSON from the LLM but currently receives a string and stores `{}` instead. The STT 400s and corrupted audio symptoms are consistent with the fixed temp filename and extension/content-type mismatch in the audio download/transcribe path.