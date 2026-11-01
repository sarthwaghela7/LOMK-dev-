"""
Corrective migration for calls.fd_processing_status.

Why this migration is required:
    The backend SQLAlchemy model in storage/models.py defines a
    `fd_processing_status` column on the `calls` table, but the Supabase schema
    may be missing that column. That causes the backend to fail when it tries to
    read or update the field during FlintDesk call processing.

What this migration adds:
    Adds `fd_processing_status` to `calls` if it does not already exist.

Exact model match:
    storage/models.py defines:
        fd_processing_status = Column(Text, nullable=True, server_default="not processed")

    So the database column must be:
        TEXT
        nullable = True
        default = 'not processed'

    We intentionally do NOT add a NOT NULL constraint or a custom CHECK
    constraint here because those are not present in the SQLAlchemy model.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from storage.db import engine


def migrate_fix_fd_processing_status():
    """Ensure calls.fd_processing_status exists with the model-compatible schema."""
    with engine.begin() as conn:
        # This migration runs on every application boot.  ALTER TABLE requests
        # an AccessExclusiveLock even with IF NOT EXISTS, and the old
        # unconditional UPDATE could scan/write the whole calls table.  Both
        # operations blocked Supabase/PostgREST schema refreshes on a schema
        # that was already current.
        has_column = conn.execute(text("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'calls'
                  AND column_name = 'fd_processing_status'
            )
        """)).scalar()
        if has_column:
            print("✓ calls.fd_processing_status already exists")
            return

        # Only an actually old database needs DDL.  The default applies to
        # future rows, and callers already treat a legacy NULL as not processed.
        conn.execute(text(
            "ALTER TABLE calls "
            "ADD COLUMN fd_processing_status TEXT DEFAULT 'not processed'"
        ))
        print("✓ Added calls.fd_processing_status")


if __name__ == "__main__":
    migrate_fix_fd_processing_status()
    print("Migration complete.")
