"""Add editable client_name/client_email fields as the source of truth for a client_id."""

from sqlalchemy import text

from storage.db import engine


def migrate_add_client_profile_fields():
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS client_name TEXT"
        ))
        connection.execute(text(
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS client_email TEXT"
        ))
