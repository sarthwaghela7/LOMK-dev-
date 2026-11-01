"""Store the agent name an agent types in FlintDesk in its own column.

front_desk_person_name is also written by the AutoFlint model, which can only
produce "N/A" or blank for it. The new column is written only by FlintDesk
edits, never by the model. A trigger copies it back into front_desk_person_name
whenever that column is blank or a placeholder, so every existing reader (and
AutoFlint) keeps seeing the typed name even if some job clears it.
"""

from sqlalchemy import text

from storage.db import engine

PLACEHOLDERS = "'', 'n/a', 'na', '-', 'none', 'unassigned'"


def migrate_manual_agent_name():
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE call_analysis_results ADD COLUMN IF NOT EXISTS front_desk_person_name_manual TEXT"
        ))
        connection.execute(text(f"""
            CREATE OR REPLACE FUNCTION keep_manual_agent_name() RETURNS trigger AS $$
            BEGIN
                IF NEW.front_desk_person_name_manual IS NOT NULL
                   AND btrim(NEW.front_desk_person_name_manual) <> ''
                   AND (NEW.front_desk_person_name IS NULL
                        OR lower(btrim(NEW.front_desk_person_name)) IN ({PLACEHOLDERS})) THEN
                    NEW.front_desk_person_name := NEW.front_desk_person_name_manual;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        connection.execute(text(
            "DROP TRIGGER IF EXISTS trg_keep_manual_agent_name ON call_analysis_results"
        ))
        connection.execute(text("""
            CREATE TRIGGER trg_keep_manual_agent_name
            BEFORE INSERT OR UPDATE ON call_analysis_results
            FOR EACH ROW EXECUTE FUNCTION keep_manual_agent_name()
        """))
