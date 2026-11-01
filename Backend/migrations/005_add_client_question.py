"""
Database migration: add call_analysis_results.client_question for the
Client FAQ page.

Adds the column (Text, nullable) and registers it as an LLM analysis
field in the `prompts` table (keyed exactly as "client_question" so
utils/prompt_config.py includes it in the LLM system prompt and
services/Database_sync/f2.py picks it up automatically like every other
analysis field).

Idempotent and safe to re-run.
"""
from sqlalchemy import text
from storage.db import engine


def migrate_add_client_question():
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE call_analysis_results "
            "ADD COLUMN IF NOT EXISTS client_question TEXT"
        ))
        print("✓ Ensured column call_analysis_results.client_question")

        existing = conn.execute(
            text("SELECT 1 FROM prompts WHERE key = :key"),
            {"key": "client_question"},
        ).fetchone()
        if existing:
            print("✓ Prompt row for client_question already exists")
        else:
            conn.execute(
                text(
                    "INSERT INTO prompts (key, description, notes) "
                    "VALUES (:key, :description, :notes)"
                ),
                {
                    "key": "client_question",
                    "description": (
                        "The exact question(s) the client asked the front desk "
                        "during the call, verbatim or closely paraphrased. "
                        "Use an empty string if the client did not ask a question."
                    ),
                    "notes": "Used by the Client FAQ page to list questions clients ask.",
                },
            )
            print("✓ Inserted prompt row for client_question")


if __name__ == "__main__":
    migrate_add_client_question()
    print("\n✓ Migration complete!")
