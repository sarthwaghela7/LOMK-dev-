"""
Database migration: create the `fd_users` table for FlintDesk login,
separate from the main `users` table used by the Frontend app.

Seeds one user:
  Sarth Waghela / sarthwaghela7@gmail.com / password from FD_SEED_USER_PASSWORD
  (seed skipped when that variable is unset or under 12 characters)

Idempotent and safe to re-run.
"""
import os
from uuid import uuid4

from sqlalchemy import text
from storage.db import engine
from utils.security import hash_password


def migrate_create_fd_users():
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS fd_users (
                id UUID PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                pass_hash TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT now()
            )
            """
        ))
        print("✓ Ensured fd_users table exists")

        seed_password = os.getenv("FD_SEED_USER_PASSWORD", "")
        if len(seed_password) < 12:
            print("! FD_SEED_USER_PASSWORD not set (or under 12 chars): skipping seed user")
            return

        existing = conn.execute(
            text("SELECT 1 FROM fd_users WHERE email = :email"),
            {"email": "sarthwaghela7@gmail.com"},
        ).fetchone()

        if existing:
            print("✓ Seed user sarthwaghela7@gmail.com already exists")
            return

        conn.execute(
            text(
                "INSERT INTO fd_users (id, name, email, pass_hash, is_active) "
                "VALUES (:id, :name, :email, :pass_hash, TRUE)"
            ),
            {
                "id": str(uuid4()),
                "name": "Sarth Waghela",
                "email": "sarthwaghela7@gmail.com",
                "pass_hash": hash_password(seed_password),
            },
        )
        print("✓ Seeded fd_users with Sarth Waghela")


if __name__ == "__main__":
    migrate_create_fd_users()
    print("\n✓ Migration complete!")
