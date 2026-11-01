"""Store the call recording filename Vitel now supplies in its CDR metadata.

Vitel's ``callrecordfilename`` is the authoritative name of a call's
recording.  F1 persists it on the call so audio resolution can use it directly
(see services/Database_sync/f1.py) instead of re-deriving the filename from
timestamps/durations, and so the idempotent same-day rematch path (which does
not call Vitel) can still use it.  Additive and nullable: existing calls are
untouched and keep using the legacy audio matcher.
"""

from sqlalchemy import text

from storage.db import engine


def migrate_add_vitel_recording_filename():
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE calls ADD COLUMN IF NOT EXISTS vitel_recording_filename TEXT"
        ))


if __name__ == "__main__":
    migrate_add_vitel_recording_filename()
