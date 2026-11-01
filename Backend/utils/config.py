import os
from pathlib import Path

from dotenv import load_dotenv


# ------------------------------------------------------------------
# Environment
# ------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


# ------------------------------------------------------------------
# Database / application
# ------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")


# ------------------------------------------------------------------
# STT
# ------------------------------------------------------------------
STT_API_KEY = os.getenv("STT_API_KEY")
STT_MODEL = os.getenv("STT_MODEL")
STT_BASE_URL = os.getenv("STT_BASE_URL")

# ElevenLabs enforces a max-concurrent-requests limit per plan (not RPM like
# OpenAI). Unconfirmed until checked on the ElevenLabs dashboard, so this
# defaults to a conservative 5; raise via env once the real plan limit is known.
ELEVENLABS_CONCURRENCY_LIMIT = int(os.getenv("ELEVENLABS_CONCURRENCY_LIMIT", "5"))


# ------------------------------------------------------------------
# AWS / S3
# ------------------------------------------------------------------
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN")
AWS_REGION = os.getenv("AWS_REGION")
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")


# ------------------------------------------------------------------
# Supabase Storage (call/case attachments only; service role key is
# backend-only and must never be sent to the frontend)
# ------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "call-attachments")
MAX_ATTACHMENT_SIZE_MB = float(os.getenv("MAX_ATTACHMENT_SIZE_MB", "5"))


# ------------------------------------------------------------------
# LLM
# ------------------------------------------------------------------
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.4-mini")
LLM_URL = (
    os.getenv("LLM_URL")
    or os.getenv("LLM_BASE_URL")
    or "https://api.openai.com/v1/chat/completions"
)

# Confirmed OpenAI account tier limits for LLM_MODEL (set from the OpenAI
# dashboard -> Limits page for the exact model used in fd_llm_node.py).
# These bound both the F3 worker pool and the shared rate limiter, so
# concurrency can never issue more requests/tokens per minute than the
# account actually allows.
# Confirmed OpenAI account tier limits for LLM_MODEL. Self-detected with
# zero manual input: explicit env vars win, otherwise a cached/live probe of
# OpenAI's own rate-limit headers is used, falling back to safe defaults.
# See utils/openai_limits.py. These bound both the F3 worker pool and the
# shared rate limiter, so concurrency can never issue more requests/tokens
# per minute than the account actually allows.
from utils.openai_limits import detect_openai_limits

OPENAI_RPM_LIMIT, OPENAI_TPM_LIMIT, OPENAI_LIMITS_SOURCE = detect_openai_limits(
    BASE_DIR, LLM_API_KEY, LLM_URL, LLM_MODEL
)
F3_LLM_MAX_RETRIES = int(os.getenv("F3_LLM_MAX_RETRIES", "5"))
F3_LLM_BACKOFF_BASE_SECONDS = float(os.getenv("F3_LLM_BACKOFF_BASE_SECONDS", "1.0"))
F3_LLM_BACKOFF_MAX_SECONDS = float(os.getenv("F3_LLM_BACKOFF_MAX_SECONDS", "30.0"))


# ------------------------------------------------------------------
# Application mode
# ------------------------------------------------------------------
# Kept for backward compatibility with any existing code that may
# still import PROCESSING_APP.
#
# IMPORTANT:
# The shared scheduler no longer uses PROCESSING_APP to decide
# whether FlintDesk or AutoFlint should run.
#
# The scheduler explicitly runs:
#
#   - FlintDesk F1 -> F2 -> F3 every minute
#   - AutoFlint F3 once per day
#
# Therefore the shared Render backend does NOT need:
#
#   PROCESSING_APP=flintdesk
#
# Keeping this variable here prevents breaking any unrelated existing
# imports or code that may still reference it.
PROCESSING_APP = (
    os.getenv("PROCESSING_APP")
    or os.getenv("APP_MODE")
    or "autoflint"
).strip().lower()


def _bounded_worker_count(name, default, maximum=4):
    try:
        return max(1, min(int(os.getenv(name, default)), maximum))
    except (TypeError, ValueError):
        return default


# Conservative per-process limits. They increase throughput without consuming
# the whole database pool or creating an unbounded external-API burst.
F1_IO_WORKERS = _bounded_worker_count("F1_IO_WORKERS", 4, 6)
# Background workers share the same SQLAlchemy pool as live API requests.
# Keep each stage below its external-provider limit by default so a burst of
# failed/retried calls cannot starve the Calls, Leads, or Tasks pages.  The
# environment variable can still lower either value for a smaller deployment.
F2_STT_WORKERS = _bounded_worker_count("F2_STT_WORKERS", min(2, ELEVENLABS_CONCURRENCY_LIMIT), 2)

# F3_LLM_WORKERS must never exceed what OPENAI_RPM_LIMIT can sustain even if
# every worker is issuing one request per second (worst case for short
# calls). This caps concurrency at the confirmed/detected tier instead of a
# fixed guess; the shared token bucket in rate_limiter.py still throttles the
# actual request rate on top of this.
_rpm_safe_workers = max(1, OPENAI_RPM_LIMIT // 60)
F3_LLM_WORKERS = _bounded_worker_count("F3_LLM_WORKERS", min(3, _rpm_safe_workers), 3)
PIPELINE_FALLBACK_BATCH_SIZE = max(
    1, min(_bounded_worker_count("PIPELINE_FALLBACK_BATCH_SIZE", 25, 100), 100)
)

# The Missed Calls page's Called Back view checks each matched callback
# call's audio file size on S3 (see routes/flintdesk/tasks.py's
# _audio_byte_size) -- one HEAD request per row. Run those concurrently
# instead of sequentially in a request-time HTTP handler; a request-time
# path needs a smaller cap than the background sync workers above.
MISSED_CALLS_S3_WORKERS = _bounded_worker_count("MISSED_CALLS_S3_WORKERS", 8, 12)


# ------------------------------------------------------------------
# Vitel
# ------------------------------------------------------------------
VITEL_USERNAME = os.getenv("VITEL_USERNAME")
VITEL_PASSWORD = os.getenv("VITEL_PASSWORD")
VITEL_LINE = os.getenv("VITEL_LINE")
VITEL_BASE_URL = os.getenv("VITEL_BASE_URL")


# ------------------------------------------------------------------
# Google Sheet lead sync (disabled until explicitly enabled)
# ------------------------------------------------------------------
# GOOGLE_SERVICE_ACCOUNT_JSON holds the key's JSON content, never a file path.
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
LEAD_SHEET_SYNC_WORKSHEET = os.getenv("LEAD_SHEET_SYNC_WORKSHEET", "All Leads")
LEAD_SHEET_SYNC_ENABLED = os.getenv("LEAD_SHEET_SYNC_ENABLED", "false").lower() == "true"
# Rows dated before this day (YYYY-MM-DD) are never imported; blank = no cutoff.
LEAD_SHEET_SYNC_START_DATE = os.getenv("LEAD_SHEET_SYNC_START_DATE", "2026-10-10")
# Teams Workflows webhook URL (a secret); blank = no notification.
LEAD_SHEET_TEAMS_WEBHOOK_URL = os.getenv("LEAD_SHEET_TEAMS_WEBHOOK_URL", "")
# Teams posts at most one message per this many minutes; leads that arrive in between wait and are sent together.
try:
    LEAD_SHEET_TEAMS_MIN_INTERVAL_MINUTES = max(0, int(os.getenv("LEAD_SHEET_TEAMS_MIN_INTERVAL_MINUTES", "10")))
except ValueError:
    LEAD_SHEET_TEAMS_MIN_INTERVAL_MINUTES = 10


# ------------------------------------------------------------------
# Required environment validation
# ------------------------------------------------------------------
if not DATABASE_URL:
    raise Exception("DATABASE_URL missing in .env")

if not SECRET_KEY:
    raise Exception("SECRET_KEY missing in .env")

# Stage 2 AI automation (Self Action Item, Action Due Date, Follow Up Dates). Off until enabled.
STAGE2_AI_ENABLED = os.getenv("FD_STAGE2_AI_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
# Background job that has the model read each OPEN call (transcript + Self Action Item) and record whose action is pending
# (services/dependency_ai.py). Off by default: it spends LLM requests.
OPEN_CALL_DEPENDENCY_AI_ENABLED = os.getenv("OPEN_CALL_DEPENDENCY_AI_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
