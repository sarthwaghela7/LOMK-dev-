"""Inspect/apply the requested website extensions; no schema changes."""
import argparse
import os
from pathlib import Path
from uuid import uuid4

import psycopg2
from dotenv import load_dotenv

ASSIGNMENTS = {"203": "Christobel", "204": "Edgin", "205": "Gracy"}
NUMBER = "2243939098"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    with psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout='10s'")
            cursor.execute("SET LOCAL lock_timeout='2s'")
            if args.apply:
                cursor.execute("SELECT pg_advisory_xact_lock(739412807)")
            for extension, name in ASSIGNMENTS.items():
                cursor.execute("SELECT id FROM fd_users WHERE lower(trim(name))=lower(%s) AND is_active=true", (name,))
                users = cursor.fetchall()
                cursor.execute("SELECT agent_id,number FROM fd_assignments WHERE extension=%s AND is_active=true", (extension,))
                existing = cursor.fetchall()
                print(f"{extension}: {name}; matching active accounts={len(users)}; active assignments={len(existing)}")
                if not args.apply:
                    continue
                if len(users) != 1:
                    raise RuntimeError(f"Need one existing active account named {name}; no accounts are created by this script")
                agent_id = users[0][0]
                if any(owner != agent_id or number != NUMBER for owner, number in existing):
                    raise RuntimeError(f"Extension {extension} already belongs to a different agent/number")
                cursor.execute("""INSERT INTO fd_sync_filters (id,filter_type,value,parent_number,target_page)
                    VALUES (%s,'extension',%s,%s,'process_calls') ON CONFLICT DO NOTHING""",
                    (str(uuid4()), extension, NUMBER))
                if not existing:
                    cursor.execute("""INSERT INTO fd_assignments
                        (id,assignment_date,extension,number,agent_id,created_at,expires_at,is_active)
                        VALUES (%s,(now() AT TIME ZONE 'America/Chicago')::date,%s,%s,%s,now(),NULL,true)""",
                        (str(uuid4()), extension, NUMBER, agent_id))
            if args.apply:
                print("Configuration committed after all three assignments validate.")
            else:
                cursor.execute("""SELECT extension,count(*),count(audio_s3_uri),
                    count(*) FILTER (WHERE transcript IS NOT NULL AND transcript <> '')
                    FROM calls WHERE call_date=(now() AT TIME ZONE 'America/Chicago')::date
                    AND extension IN ('203','204','205') GROUP BY extension ORDER BY extension""")
                for extension, calls, audio, transcripts in cursor.fetchall():
                    print(f"Today ext {extension}: calls={calls}, audio-linked={audio}, transcripts={transcripts}")


if __name__ == "__main__":
    main()
