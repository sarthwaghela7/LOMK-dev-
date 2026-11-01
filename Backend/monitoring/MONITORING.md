# Daily usage & cost monitoring

Run: `cd AutoFlint_Backend && python -m monitoring --help`

```
python -m monitoring --check-providers                        # read-only access probe, prints PASS/FAIL + reason
python -m monitoring --dry-run                                # yesterday (MONITOR_REPORT_TZ), prints only
python -m monitoring --date 2026-10-08 --csv out/usage.csv \
    --xlsx-template ~/Downloads/Daily_AI_Usage_and_Cost_Tracker.xlsx   # -> *_updated.xlsx next to the template
python -m monitoring --date 2026-10-08 --mtd railway=3.52 --mtd supabase=11.40   # dashboard month-to-date totals
```
Re-running a date updates its row in `usage_daily_reports` (revision+1). CSV/XLSX skip an existing date unless
`--replace-existing`, which never blanks a cell that already has a value. The template is never modified unless `--in-place` (backup first).

## Metric definitions (Date = day in `MONITOR_REPORT_TZ`, default UTC — all providers bill in UTC days)
| Column | Source | Notes |
|---|---|---|
| Calls Ingested | `calls.registered_at` | call time; the table has no insert timestamp, so late-synced calls can raise a past day on re-run |
| Calls STT Completed | `provider_usage_events` (new) | DISTINCT calls with a successful STT attempt — retries/re-runs count once |
| Audio Minutes Sent to STT | same, `audio_seconds` | WAV header of the exact bytes uploaded; every successful attempt is summed (a re-run is billed again); failed attempts excluded and disclosed |
| STT Cost | minutes × `MONITOR_STT_PRICE_PER_HOUR` | **Estimated**; blank if price unset |
| LLM Analyses Completed | `analysis_jobs` status=completed, by `updated_at` | one job per call/app/request_key, so retries cannot double count |
| LLM Input/Output Tokens | OpenAI response `usage` via ledger; fallback `analysis_jobs.response.usage` | sources are never summed on one day; missing usage stays blank-aware |
| LLM Cost | OpenAI `/v1/organization/costs` (**Actual**); else tokens × `MONITOR_LLM_PRICE_*` (**Estimated**) | |
| Railway Cost | `--mtd railway=` snapshots → daily difference | **Actual**; no verified per-day cost API |
| Railway Usage / Metric | Railway GraphQL `usage` (best-effort, unverified schema) | real resource units, not request counts |
| Supabase Cost | `--mtd supabase=` difference (**Actual**) or `MONITOR_SUPABASE_FIXED_MONTHLY_USD`/days (**Estimated**) | |
| Supabase DB Size | `pg_database_size()` in a READ ONLY transaction | GiB, read at collection time |
| Supabase Egress | — | no verified API: manual |
| S3 Storage (GB) | CloudWatch `BucketSizeBytes` for `AWS_S3_BUCKET_NAME` only | |
| S3 Requests/Transfer, Storage Cost | Cost Explorer (opt-in), split by usage type | whole-S3-service unless cost-allocation tag configured; credits/refunds/tax excluded and stated |
| Total / Cost per STT call | sum of components that have values / Calls STT Completed | tracker formulas in R,S are left alone; Notes says PARTIAL when a component is missing |
| STT Failures | ledger | DISTINCT calls with a failed attempt and no success that day (no-audio skips never reach the provider and are excluded) |
| LLM Failures | `analysis_jobs` status=review_required | |
| Billing Data Type | tracker dropdown | `Actual` only if all six components are present and actual; any estimate or gap → `Estimated`; none → `Not Available` |

## Environment variables (names only)
Required by the app already: `DATABASE_URL`, `AWS_*`, `AWS_S3_BUCKET_NAME`, `STT_*`, `LLM_*`.
Optional: `MONITOR_REPORT_TZ`, `OPENAI_ADMIN_KEY`, `OPENAI_PROJECT_ID`, `MONITOR_LLM_PRICE_INPUT_PER_1M`, `_CACHED_INPUT_PER_1M`, `_OUTPUT_PER_1M`,
`MONITOR_STT_PRICE_PER_HOUR`, `MONITOR_ELEVENLABS_USAGE_ENABLED`, `ELEVENLABS_USAGE_API_KEY`, `RAILWAY_API_TOKEN`, `RAILWAY_PROJECT_ID`,
`MONITOR_SUPABASE_FIXED_MONTHLY_USD`, `MONITOR_AWS_COST_EXPLORER_ENABLED`, `MONITOR_AWS_COST_TAG_KEY/_VALUE`, `MONITOR_AWS_INCLUDE_ADJUSTMENTS`,
`MONITOR_SPIKE_RATIO`, `MONITOR_SPIKE_MIN_USD`, `MONITOR_FAILURE_MIN_COUNT`, `MONITOR_FAILURE_RATIO`.

## Access to configure manually
* OpenAI: an **Admin API key** (org owner creates it; project keys cannot read usage/costs).
* ElevenLabs (optional reconciliation): API key with usage permission.
* Railway: account/team API token + project id.
* AWS: `cloudwatch:ListMetrics`, `cloudwatch:GetMetricStatistics`; for costs `ce:GetCostAndUsage` (AWS bills ~$0.01 per Cost Explorer call), and a cost-allocation tag on the bucket if you want the bucket isolated.
* Deploy: run `python -m migrations.runner` (adds migration 071) **before** deploying the backend change; until then the ledger hooks silently no-op.

## Production code touched (all best-effort, can never raise)
`services/Database_sync/f2.py` (wraps the STT call in `stt_usage_scope`), `services/Processing_pipeline/stt_node.py` (reports audio seconds),
`services/Processing_pipeline/fd_llm_node.py` (one ledger row per successful response), `migrations/runner.py` (+1 manifest line).
Not instrumented: `/ai/transcribe`, `/ai/narrative`, manual-data-score (direct `requests` calls) — their OpenAI cost is still in the Costs API figure, and the reconciliation warning will show the token gap.
