# FlintDesk concurrency and latency audit

## Implemented from static evidence

- Dependencies routes now use normal synchronous FastAPI handlers, so FastAPI
  runs synchronous SQLAlchemy work in its thread pool instead of blocking the
  event loop.
- The synchronous `requests` calls in the AI proxy are likewise thread-pooled.
- Dependency list identifier resolution is batched: one Call query and one
  Lead query replace per-row Call/Lead lookups. Dependency agents are eager
  loaded.
- Lead list call resolution is batched instead of querying once per lead.
- Calls, analysis, leads, dependencies, and tasks accept bounded page/page-size
  parameters (maximum 500). Parameters are optional to preserve current client
  behavior until UI pagination is rolled out.
- Task date and owner filters are applied in SQL before rows are materialized.

## Queue findings

F1, F2, FlintDesk F3, and AutoFlint F3 are independent APScheduler jobs. A
stalled category does not block the other categories, but each category uses
`max_instances=1`, so one stalled run delays later runs of that same category.
External STT and LLM calls have finite timeouts.

No durable distributed queue exists. Moving user-triggered Analyse/import work
to FastAPI `BackgroundTasks` or an in-memory executor would lose work during a
restart and cannot coordinate multiple web processes. A reliable queue requires
a durable job table with atomic claiming or external queue infrastructure plus
separate workers. That deployment-level change is intentionally not presented
as completed here.

## Pending staging validation

No throughput or latency improvement is claimed. Staging load tests must verify
the N+1 reduction, page sizing, thread-pool behavior, connection saturation,
and whether worker concurrency should change. The SQLAlchemy pool remains 5
persistent plus 10 overflow connections per process because Render worker count
and Supabase/PgBouncer limits are still unknown.
