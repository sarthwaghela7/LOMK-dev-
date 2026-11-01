import os
import logging
import time
import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

# Route imports
from routes import auth, calls
from routes.fd_auth import router as fd_auth_router
from routes.users import router as users_router
from routes.audio import router as audio_router
from routes.prompts import router as prompts_router
from routes.ai_proxy import router as ai_proxy_router
from routes.flintdesk.calls import router as flintdesk_router
from routes.flintdesk.sync_filters import router as flintdesk_sync_filters_router
from routes.flintdesk.dependencies import router as flintdesk_dependencies_router
from routes.flintdesk.leads import router as flintdesk_leads_router
from routes.flintdesk.sm_leads import router as flintdesk_sm_leads_router
from routes.flintdesk.call_updates import router as flintdesk_call_updates_router
from routes.flintdesk.tasks import router as flintdesk_tasks_router
from routes.flintdesk.todos import router as flintdesk_todos_router
from routes.flintdesk.visa_updates import router as flintdesk_visa_updates_router
from routes.flintdesk.dashboard_metrics import router as flintdesk_dashboard_metrics_router
from routes.flintdesk.call_count_reconciliation import router as flintdesk_call_count_reconciliation_router
from routes.fd_users import router as fd_users_router
from routes.fd_assignments import router as fd_assignments_router
from routes.flintdesk.fd_prompts import router as flintdesk_fd_prompts_router
from routes.flintdesk.case_closures import router as flintdesk_case_closures_router
from routes.flintdesk.priority_cases import router as flintdesk_priority_cases_router
from routes.flintdesk.follow_ups import router as flintdesk_follow_ups_router
from routes.flintdesk.assignment_counts import router as flintdesk_assignment_counts_router

# Scheduler
from scheduler.jobs import start_scheduler
from storage.db import engine

# Create FastAPI application.  ``app`` is assigned to the global CORS wrapper
# after routes are registered; keep this inner application available while
# configuring routers and exception handlers.
# The interactive docs and OpenAPI schema publish every route to anyone who can
# reach the API, so they are off unless explicitly enabled for local work.
_docs_enabled = os.getenv("ENABLE_API_DOCS", "").strip().lower() in {"1", "true", "yes"}
api = FastAPI(
    title="AutoFlint Backend",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
logger = logging.getLogger("autoflint.api")

# Page loads issue several independent read requests. Without a gate, a few
# browsers can make every one acquire a DB connection at once, leaving none
# for login. Queue excess reads before routing/dependencies acquire a session.
try:
    MAX_CONCURRENT_DATA_REQUESTS = max(2, min(int(os.getenv("MAX_CONCURRENT_DATA_REQUESTS", "8")), 16))
except ValueError:
    MAX_CONCURRENT_DATA_REQUESTS = 8
_data_request_gate = asyncio.Semaphore(MAX_CONCURRENT_DATA_REQUESTS)


def _is_throttled_data_request(request: Request) -> bool:
    if request.method != "GET":
        return False
    # Login and writes are intentionally never queued behind table reads.
    return request.url.path.startswith(("/calls", "/api/", "/flintdesk/", "/fd-users"))


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


@api.middleware("http")
async def log_slow_requests(request: Request, call_next):
    """Expose slow endpoint paths and current pool pressure in Railway logs."""
    started_at = time.perf_counter()
    if _is_throttled_data_request(request):
        async with _data_request_gate:
            response = await call_next(request)
    else:
        response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and response.status_code < 400:
        from utils.ttl_cache import invalidate_read_caches
        invalidate_read_caches()
    if elapsed_ms >= 2_000:
        logger.warning(
            "[http] slow_request_ms=%.1f method=%s path=%s status=%s pool=%s",
            elapsed_ms,
            request.method,
            request.url.path,
            response.status_code,
            engine.pool.status(),
        )
    return response


def cors_allowed_origins() -> list[str]:
    """Return trusted browser origins without allowing arbitrary websites.

    Render can provide additional comma-separated origins through
    CORS_ALLOWED_ORIGINS (for example a temporary preview domain).  The
    production AutoFlint domain is always included so a missing Render
    variable cannot break the deployed UI.
    """
    defaults = ["https://autoflint.onrender.com", "https://flintdesk.onrender.com"]
    configured = os.getenv("CORS_ALLOWED_ORIGINS", "")
    extras = [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
    return list(dict.fromkeys([*defaults, *extras]))


def apply_required_flintdesk_migrations():
    """Compatibility entry point for explicit release tooling, never startup."""
    from migrations.runner import run_migrations
    run_migrations()


def ensure_attachment_storage_bucket():
    """Best-effort: create the private Supabase Storage bucket if missing.

    Non-fatal on failure so a Storage outage/misconfiguration never blocks
    the rest of the API from starting.
    """
    try:
        from storage.supabase_storage import SupabaseStorageService
        SupabaseStorageService().ensure_bucket_exists()
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] Supabase Storage bucket check skipped: {exc}")


# ----------------------------------------
# Startup Event
# # ----------------------------------------
@api.on_event("startup")
async def startup_event():
       # Schema changes belong in the release command: python -m migrations.runner.
       # RUN_STARTUP_MIGRATIONS is intentionally no longer honored.
       logger.info("[startup] revision=%s deployment=%s replica=%s pid=%s",
                   os.getenv("RAILWAY_GIT_COMMIT_SHA", "unknown"),
                   os.getenv("RAILWAY_DEPLOYMENT_ID", "unknown"),
                   os.getenv("RAILWAY_REPLICA_ID", "unknown"), os.getpid())
       ensure_attachment_storage_bucket()
       start_scheduler()

# ----------------------------------------
# Compresses JSON responses (calls/analysis endpoints can return large
# arrays) so they transfer faster over the network — a big share of the
# "buffering" on a slow/starter-tier host is just bytes-on-the-wire time.
api.add_middleware(GZipMiddleware, minimum_size=500)

# ----------------------------------------
# Register Routes
# ----------------------------------------
api.include_router(auth.router)
api.include_router(calls.router)
api.include_router(fd_auth_router)
api.include_router(users_router)
api.include_router(audio_router)
api.include_router(prompts_router)
api.include_router(ai_proxy_router)
api.include_router(flintdesk_router)
api.include_router(flintdesk_sync_filters_router)
api.include_router(flintdesk_dependencies_router)
api.include_router(flintdesk_leads_router)
api.include_router(flintdesk_sm_leads_router)
api.include_router(flintdesk_call_updates_router)
api.include_router(flintdesk_tasks_router)
api.include_router(flintdesk_todos_router)
api.include_router(flintdesk_visa_updates_router)
api.include_router(flintdesk_dashboard_metrics_router)
api.include_router(flintdesk_call_count_reconciliation_router)
api.include_router(fd_users_router)
api.include_router(fd_assignments_router)

# ----------------------------------------
# Root Endpoint
# ----------------------------------------
@api.get("/")
def root():
    """
    Health check endpoint.
    """
    return {
        "status": "ok",
        "build_revision": os.getenv("RAILWAY_GIT_COMMIT_SHA", os.getenv("APP_BUILD_REVISION", "unknown")),
    }


@api.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Log unexpected failures while keeping implementation details private."""
    logger.exception("Unhandled API error for %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# This must wrap the *entire* FastAPI application, rather than only its route
# middleware stack.  Otherwise ServerErrorMiddleware can generate a 500 after
# CORSMiddleware has been bypassed, and browsers hide the useful HTTP error as
# a misleading CORS failure.
app = CORSMiddleware(
    app=api,
    allow_origins=cors_allowed_origins(),
    # Keep local Vite development supported without opening production to an
    # arbitrary remote origin.  This matches localhost/127.0.0.1 on any port.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(?::\d+)?",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
api.include_router(flintdesk_fd_prompts_router)
api.include_router(flintdesk_case_closures_router)
api.include_router(flintdesk_priority_cases_router)
api.include_router(flintdesk_follow_ups_router)
api.include_router(flintdesk_assignment_counts_router)
