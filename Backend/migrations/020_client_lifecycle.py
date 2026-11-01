"""Add permanent clients and safely backfill historical call relationships."""

from sqlalchemy import text

from storage.db import engine


def migrate_client_lifecycle():
    with engine.begin() as conn:
        already_applied = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='calls' AND column_name='client_id'
        """)).scalar()

        if already_applied:
            # The one-time backfill below scans every call row and exceeds the
            # 30s statement timeout; new calls get a client via
            # services.client_lifecycle at ingestion time.
            print("✓ Client lifecycle schema already exists")
            return

        conn.execute(text("CREATE SEQUENCE IF NOT EXISTS clients_client_id_seq START 1"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS clients (
                client_id TEXT PRIMARY KEY,
                phone_number TEXT NOT NULL UNIQUE,
                first_call_date DATE NOT NULL,
                last_call_date DATE NOT NULL,
                total_calls INTEGER NOT NULL DEFAULT 0,
                client_status TEXT NOT NULL DEFAULT 'Open'
                    CHECK (client_status IN ('Open', 'Pending', 'Closed', 'Reopened')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("ALTER TABLE calls ADD COLUMN IF NOT EXISTS client_id TEXT"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_clients_phone_number ON clients(phone_number)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_calls_client_id_call_date ON calls(client_id, call_date DESC, call_time DESC)"))

        # Match the central service: normalize extensions away, prefer the
        # non-FlintDesk configured number, then use source as a safe fallback.
        conn.execute(text("""
            WITH system_numbers AS (
                SELECT CASE
                    WHEN length(regexp_replace(value, '\\D', '', 'g')) = 11
                         AND regexp_replace(value, '\\D', '', 'g') LIKE '1%'
                        THEN substring(regexp_replace(value, '\\D', '', 'g') FROM 2)
                    WHEN length(regexp_replace(value, '\\D', '', 'g')) = 12
                         AND regexp_replace(value, '\\D', '', 'g') LIKE '91%'
                        THEN substring(regexp_replace(value, '\\D', '', 'g') FROM 3)
                    ELSE regexp_replace(value, '\\D', '', 'g') END AS number
                FROM fd_sync_filters WHERE filter_type = 'number'
            ), normalized_calls AS (
                SELECT c.call_id, c.call_date, c.call_time,
                    CASE
                        WHEN src_number IN (SELECT number FROM system_numbers)
                             AND dst_number NOT IN (SELECT number FROM system_numbers) THEN dst_number
                        WHEN dst_number IN (SELECT number FROM system_numbers)
                             AND src_number NOT IN (SELECT number FROM system_numbers) THEN src_number
                        ELSE COALESCE(NULLIF(src_number, ''), NULLIF(dst_number, '')) END AS phone_number
                FROM (
                    SELECT calls.*, 
                        CASE WHEN length(regexp_replace(src, '\\D', '', 'g')) = 11 AND regexp_replace(src, '\\D', '', 'g') LIKE '1%'
                             THEN substring(regexp_replace(src, '\\D', '', 'g') FROM 2)
                             WHEN length(regexp_replace(src, '\\D', '', 'g')) = 12 AND regexp_replace(src, '\\D', '', 'g') LIKE '91%'
                             THEN substring(regexp_replace(src, '\\D', '', 'g') FROM 3)
                             ELSE regexp_replace(src, '\\D', '', 'g') END AS src_number,
                        CASE WHEN length(regexp_replace(dst, '\\D', '', 'g')) = 11 AND regexp_replace(dst, '\\D', '', 'g') LIKE '1%'
                             THEN substring(regexp_replace(dst, '\\D', '', 'g') FROM 2)
                             WHEN length(regexp_replace(dst, '\\D', '', 'g')) = 12 AND regexp_replace(dst, '\\D', '', 'g') LIKE '91%'
                             THEN substring(regexp_replace(dst, '\\D', '', 'g') FROM 3)
                             ELSE regexp_replace(dst, '\\D', '', 'g') END AS dst_number
                    FROM calls
                ) c
            ), grouped AS (
                SELECT phone_number, min(call_date) AS first_call_date,
                       max(call_date) AS last_call_date, count(*) AS total_calls
                FROM normalized_calls
                WHERE phone_number IS NOT NULL AND length(phone_number) >= 7
                GROUP BY phone_number
            )
            INSERT INTO clients (client_id, phone_number, first_call_date, last_call_date, total_calls, client_status, created_at, updated_at)
            SELECT 'CL-' || lpad(nextval('clients_client_id_seq')::text, 6, '0'), phone_number,
                   first_call_date, last_call_date, total_calls, 'Open', now(), now()
            FROM grouped
            ON CONFLICT (phone_number) DO UPDATE SET
                first_call_date = LEAST(clients.first_call_date, EXCLUDED.first_call_date),
                last_call_date = GREATEST(clients.last_call_date, EXCLUDED.last_call_date),
                total_calls = EXCLUDED.total_calls,
                updated_at = now()
        """))
        conn.execute(text("""
            WITH system_numbers AS (
                SELECT CASE
                    WHEN length(regexp_replace(value, '\\D', '', 'g')) = 11 AND regexp_replace(value, '\\D', '', 'g') LIKE '1%'
                        THEN substring(regexp_replace(value, '\\D', '', 'g') FROM 2)
                    WHEN length(regexp_replace(value, '\\D', '', 'g')) = 12 AND regexp_replace(value, '\\D', '', 'g') LIKE '91%'
                        THEN substring(regexp_replace(value, '\\D', '', 'g') FROM 3)
                    ELSE regexp_replace(value, '\\D', '', 'g') END AS number
                FROM fd_sync_filters WHERE filter_type = 'number'
            ), normalized AS (
                SELECT call_id,
                    CASE WHEN length(regexp_replace(src, '\\D', '', 'g')) = 11 AND regexp_replace(src, '\\D', '', 'g') LIKE '1%' THEN substring(regexp_replace(src, '\\D', '', 'g') FROM 2)
                         WHEN length(regexp_replace(src, '\\D', '', 'g')) = 12 AND regexp_replace(src, '\\D', '', 'g') LIKE '91%' THEN substring(regexp_replace(src, '\\D', '', 'g') FROM 3)
                         ELSE regexp_replace(src, '\\D', '', 'g') END AS src_number,
                    CASE WHEN length(regexp_replace(dst, '\\D', '', 'g')) = 11 AND regexp_replace(dst, '\\D', '', 'g') LIKE '1%' THEN substring(regexp_replace(dst, '\\D', '', 'g') FROM 2)
                         WHEN length(regexp_replace(dst, '\\D', '', 'g')) = 12 AND regexp_replace(dst, '\\D', '', 'g') LIKE '91%' THEN substring(regexp_replace(dst, '\\D', '', 'g') FROM 3)
                         ELSE regexp_replace(dst, '\\D', '', 'g') END AS dst_number
                FROM calls WHERE client_id IS NULL
            ), choices AS (
                SELECT call_id, CASE
                    WHEN src_number IN (SELECT number FROM system_numbers) THEN dst_number
                    WHEN dst_number IN (SELECT number FROM system_numbers) THEN src_number
                    ELSE src_number END AS phone_number
                FROM normalized
            )
            UPDATE calls SET client_id = clients.client_id
            FROM choices JOIN clients ON clients.phone_number = choices.phone_number
            WHERE calls.call_id = choices.call_id
        """))
        conn.execute(text("""
            DO $$ BEGIN
                ALTER TABLE calls ADD CONSTRAINT fk_calls_client_id
                FOREIGN KEY (client_id) REFERENCES clients(client_id);
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """))
        print("Ensured client lifecycle tables, backfill, and call relationship")


if __name__ == "__main__":
    migrate_client_lifecycle()
