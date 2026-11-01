"""Case lifecycle: per-call stage, In-Transition timer, history, and the Lost-batch outbox (additive and idempotent).

call_analysis_results (one row = one call record)
    case_lifecycle_stage        open | in_transition | lost | dropped | success. NULL = not categorised yet (before backfill).
    in_transition_entered_at    when THIS call entered In-Transition; kept while it moves between In-Transition statuses.
    last_in_transition_status   last valid In-Transition Case Status Change value (what " & Lost" is appended to).
    lost_at                     when the call became Lost.
    case_lifecycle_updated_at   latest stage change.

A BEFORE trigger keeps those columns right for EVERY writer of fd_m_case_status_change (the Calls page, the related-call
closure confirmation and its revert, legacy routes, imports), so the existing save and propagation code is untouched.
The stage function mirrors services/case_lifecycle.py (Tests/test_case_lifecycle.py checks they agree).

This migration does no data rewrite: existing rows keep NULL until ops/backfill_case_lifecycle.py categorises them in batches.
Safe to rerun.
"""
from sqlalchemy import text

from storage.db import engine

STAGE_FUNCTION = r"""
CREATE OR REPLACE FUNCTION fd_case_lifecycle_stage_of(status text) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE k text := lower(btrim(regexp_replace(coalesce(status, ''), '\s+', ' ', 'g')));
BEGIN
    IF k LIKE '% & lost' OR k = 'pc shared and lost' THEN RETURN 'lost'; END IF;
    IF k IN ('success pc', 'success ge', 'success - pc', 'success - ge') THEN RETURN 'success'; END IF;
    IF k IN ('client side delay', 'dropped') THEN RETURN 'dropped'; END IF;
    IF k IN ('initial email sent (fee details shared)', 'initial email follow up', 'pc link shared', 'pc follow up',
             'initial email sent.', 'initial email followup', 'fee details shared & fu') THEN RETURN 'in_transition'; END IF;
    RETURN 'open';
END $$;
"""

TRIGGER_FUNCTION = r"""
CREATE OR REPLACE FUNCTION fd_case_lifecycle_trigger() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    new_stage text := fd_case_lifecycle_stage_of(NEW.fd_m_case_status_change);
    old_stage text;
    blank boolean := btrim(coalesce(NEW.fd_m_case_status_change, '')) = '';
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.case_lifecycle_stage := new_stage;
        NEW.case_lifecycle_updated_at := now();
        IF new_stage = 'in_transition' THEN
            NEW.in_transition_entered_at := coalesce(NEW.in_transition_entered_at, now());
            NEW.last_in_transition_status := NEW.fd_m_case_status_change;
        ELSIF new_stage = 'lost' THEN
            NEW.lost_at := coalesce(NEW.lost_at, now());
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.fd_m_case_status_change IS NOT DISTINCT FROM OLD.fd_m_case_status_change AND NEW.case_lifecycle_stage IS NOT NULL THEN
        RETURN NEW;                       -- unchanged status: ordinary edits and re-saves never touch the lifecycle
    END IF;

    old_stage := coalesce(OLD.case_lifecycle_stage, fd_case_lifecycle_stage_of(OLD.fd_m_case_status_change));
    IF blank AND old_stage IN ('lost', 'dropped', 'success') THEN
        NEW.case_lifecycle_stage := old_stage;   -- a blank value never erases an established outcome
        RETURN NEW;
    END IF;

    NEW.case_lifecycle_stage := new_stage;
    IF new_stage = 'in_transition' THEN
        IF old_stage <> 'in_transition' OR OLD.in_transition_entered_at IS NULL THEN
            NEW.in_transition_entered_at := now();          -- first entry (or re-entry after leaving): the timer starts now
        ELSE
            NEW.in_transition_entered_at := OLD.in_transition_entered_at;   -- moving between In-Transition statuses keeps it
        END IF;
        NEW.last_in_transition_status := NEW.fd_m_case_status_change;
        NEW.lost_at := NULL;
    ELSE
        NEW.in_transition_entered_at := NULL;
        IF new_stage = 'lost' THEN
            NEW.lost_at := coalesce(OLD.lost_at, now());
            NEW.last_in_transition_status := coalesce(OLD.last_in_transition_status, OLD.fd_m_case_status_change);
        ELSE
            NEW.lost_at := NULL;
        END IF;
    END IF;

    IF new_stage IS DISTINCT FROM OLD.case_lifecycle_stage THEN
        NEW.case_lifecycle_updated_at := now();
        INSERT INTO fd_case_lifecycle_events (analysis_id, call_id, from_stage, to_stage, from_status, to_status)
        VALUES (NEW.id, NEW.call_id::text, OLD.case_lifecycle_stage, new_stage, OLD.fd_m_case_status_change, NEW.fd_m_case_status_change);
    END IF;
    RETURN NEW;
END $$;
"""


def migrate_case_lifecycle():
    with engine.begin() as connection:
        for column, ddl in (
            ("case_lifecycle_stage", "TEXT"), ("in_transition_entered_at", "TIMESTAMPTZ"),
            ("last_in_transition_status", "TEXT"), ("lost_at", "TIMESTAMPTZ"), ("case_lifecycle_updated_at", "TIMESTAMPTZ"),
        ):
            connection.execute(text(f"ALTER TABLE call_analysis_results ADD COLUMN IF NOT EXISTS {column} {ddl}"))
        connection.execute(text("ALTER TABLE call_analysis_results DROP CONSTRAINT IF EXISTS call_analysis_results_lifecycle_stage_check"))
        connection.execute(text(
            "ALTER TABLE call_analysis_results ADD CONSTRAINT call_analysis_results_lifecycle_stage_check "
            "CHECK (case_lifecycle_stage IS NULL OR case_lifecycle_stage IN ('open','in_transition','lost','dropped','success')) NOT VALID"))

        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS fd_case_lifecycle_events (
                id BIGSERIAL PRIMARY KEY,
                analysis_id BIGINT NOT NULL,
                call_id TEXT,
                from_stage TEXT,
                to_stage TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS fd_case_lifecycle_events_analysis ON fd_case_lifecycle_events (analysis_id, changed_at)"))

        # Outbox: one row per Lost transition; batches of 10 are cut from the unbatched rows.
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS fd_case_lost_batches (
                id UUID PRIMARY KEY,
                sequence BIGSERIAL NOT NULL UNIQUE,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                delivered_at TIMESTAMPTZ,
                last_error TEXT
            )
        """))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS fd_case_lost_transitions (
                id BIGSERIAL PRIMARY KEY,
                analysis_id BIGINT NOT NULL,
                call_id TEXT,
                call_reference TEXT,
                in_transition_entered_at TIMESTAMPTZ NOT NULL,
                previous_status TEXT,
                lost_status TEXT,
                transitioned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                batch_id UUID REFERENCES fd_case_lost_batches(id),
                -- A call contributes once per Lost transition (= once per In-Transition entry that expired).
                CONSTRAINT fd_case_lost_transitions_once UNIQUE (analysis_id, in_transition_entered_at)
            )
        """))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS fd_case_lost_transitions_unbatched ON fd_case_lost_transitions (id) WHERE batch_id IS NULL"))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS fd_case_lost_batches_pending ON fd_case_lost_batches (next_attempt_at) WHERE delivered_at IS NULL"))

        connection.execute(text(STAGE_FUNCTION))
        connection.execute(text(TRIGGER_FUNCTION))
        connection.execute(text("DROP TRIGGER IF EXISTS fd_case_lifecycle ON call_analysis_results"))
        connection.execute(text(
            "CREATE TRIGGER fd_case_lifecycle BEFORE INSERT OR UPDATE OF fd_m_case_status_change ON call_analysis_results "
            "FOR EACH ROW EXECUTE FUNCTION fd_case_lifecycle_trigger()"))

        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS call_analysis_results_lifecycle_stage ON call_analysis_results (case_lifecycle_stage)"))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS call_analysis_results_in_transition_entered ON call_analysis_results (in_transition_entered_at) "
            "WHERE case_lifecycle_stage = 'in_transition'"))


if __name__ == "__main__":
    migrate_case_lifecycle()
