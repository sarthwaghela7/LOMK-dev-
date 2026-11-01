"""Create the explicit FlintDesk admin account and normalize legacy roles."""

import os
from uuid import uuid4

from sqlalchemy import text

from storage.db import engine
from utils.security import hash_password


ADMIN_EMAIL = "admin@gmail.com"
# Never commit a password. Supply FD_ADMIN_INITIAL_PASSWORD (min 12 chars) to
# create/reset this account; without it the admin seed is skipped.
ADMIN_PASSWORD = os.getenv("FD_ADMIN_INITIAL_PASSWORD", "")


def migrate_fd_roles():
    with engine.begin() as conn:
        if len(ADMIN_PASSWORD) < 12:
            print("! FD_ADMIN_INITIAL_PASSWORD not set (or under 12 chars): skipping admin seed")
        else:
            conn.execute(text("""
                INSERT INTO fd_users (id, name, email, pass_hash, is_active, role)
                VALUES (:id, 'Admin', :email, :pass_hash, TRUE, 'admin')
                ON CONFLICT (email) DO UPDATE SET
                    name = EXCLUDED.name,
                    pass_hash = EXCLUDED.pass_hash,
                    is_active = TRUE,
                    role = 'admin'
            """), {
                "id": str(uuid4()),
                "email": ADMIN_EMAIL,
                "pass_hash": hash_password(ADMIN_PASSWORD),
            })
        conn.execute(text("""
            UPDATE fd_users
            SET role = 'agent'
            WHERE LOWER(TRIM(name)) = 'gokul'
              AND LOWER(email) <> :admin_email
        """), {"admin_email": ADMIN_EMAIL})


if __name__ == "__main__":
    migrate_fd_roles()
