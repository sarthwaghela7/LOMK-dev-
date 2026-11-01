"""Priority Cases: make the contextual prompt (services/priority_case_prompt.py) the active fd_prompts version.

Appends the next version of 'priority_case_classification' and makes it the single active one, so a deploy that runs the
release command (python -m migrations.runner) starts using it. Rules, same spirit as migrations 069 / 074:

  * only when the CURRENT active version is still the untouched seed (updated_by = 'seed'): an administrator's edit is
    never overwritten, the migration just says so and leaves it alone;
  * skipped when a version with exactly this text already exists (safe to rerun);
  * skipped when migration 074 has not created the key yet.

It analyses nothing and changes no call data: the worker only classifies calls that have no result yet
(run_batch(reanalyse=False)), so existing results, including all October calls already analysed, are left as they are.
Roll back (manual): UPDATE fd_prompts SET is_active = (version = 1) WHERE prompt_key = 'priority_case_classification';
"""
from sqlalchemy import text

from services.priority_case_prompt import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from storage.db import engine

PROMPT_KEY = "priority_case_classification"
UPDATED_BY = "migration:075"


def migrate_priority_prompt_contextual():
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"fd_prompts:{PROMPT_KEY}"})
        rows = connection.execute(text(
            "SELECT version, is_active, updated_by, system_prompt, user_prompt_template, prompt_name "
            "FROM fd_prompts WHERE prompt_key = :k ORDER BY version"), {"k": PROMPT_KEY}).fetchall()
        if not rows:
            print(f"[075] no '{PROMPT_KEY}' prompt yet (migration 074 not applied); nothing to do.")
            return
        if any(r[3].strip() == SYSTEM_PROMPT.strip() and r[4].strip() == USER_PROMPT_TEMPLATE.strip() for r in rows):
            print("[075] the contextual prompt already exists as a version; nothing to do.")
            return
        active = next((r for r in rows if r[1]), None)
        if active is None or active[2] != "seed":
            print(f"[075] the active prompt was last set by {active[2] if active else None!r}, not the seed; "
                  "left untouched. Use ops/activate_priority_prompt.py if you want the contextual wording.")
            return
        version = max(r[0] for r in rows) + 1
        connection.execute(text("UPDATE fd_prompts SET is_active = false WHERE prompt_key = :k AND is_active"), {"k": PROMPT_KEY})
        connection.execute(text("""
            INSERT INTO fd_prompts (prompt_key, prompt_name, system_prompt, user_prompt_template,
                                    version, is_active, updated_by)
            VALUES (:k, :name, :sp, :up, :v, true, :by)
        """), {"k": PROMPT_KEY, "name": active[5], "sp": SYSTEM_PROMPT.strip(), "up": USER_PROMPT_TEMPLATE.strip(),
               "v": version, "by": UPDATED_BY})
        print(f"[075] activated contextual prompt as version {version}.")


if __name__ == "__main__":
    migrate_priority_prompt_contextual()
