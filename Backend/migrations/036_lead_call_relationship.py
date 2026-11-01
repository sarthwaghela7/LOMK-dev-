from sqlalchemy import text

from storage.db import engine


def migrate_lead_call_relationship():
    """Enforce the existing nullable, one-lead-to-one-call association."""
    with engine.begin() as connection:
        # The USING NULLIF(fd_a_leads_call_id, '')::uuid conversion below is
        # only valid when the column is still TEXT (its original, pre-
        # migration type). Once it has already been converted to UUID, that
        # same expression compares the UUID column against the text literal
        # '', which forces Postgres to cast '' to uuid and fails with
        # "invalid input syntax for type uuid: \"\"". Checking the actual
        # column type first makes this migration safe to replay on a
        # database where the conversion already happened.
        current_type = connection.execute(text("""
            SELECT data_type FROM information_schema.columns
            WHERE table_name = 'fd_leads' AND column_name = 'fd_a_leads_call_id'
        """)).scalar()

        if current_type not in (None, "uuid"):
            connection.execute(text("""
                ALTER TABLE fd_leads
                ALTER COLUMN fd_a_leads_call_id TYPE UUID
                USING NULLIF(fd_a_leads_call_id, '')::uuid
            """))
        connection.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_fd_leads_call_id
            ON fd_leads(fd_a_leads_call_id)
            WHERE fd_a_leads_call_id IS NOT NULL
        """))
        connection.execute(text("""
            DO $$ BEGIN
              ALTER TABLE fd_leads
              ADD CONSTRAINT fk_fd_leads_call_id
              FOREIGN KEY (fd_a_leads_call_id) REFERENCES calls(call_id)
              ON DELETE SET NULL;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))


if __name__ == "__main__":
    migrate_lead_call_relationship()
