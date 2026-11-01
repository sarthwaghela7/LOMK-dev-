"""Case Status Change: add "Wrong Number Dialed", classified as the Dropped lifecycle stage.

fd_m_case_status_change is free text (no enum or CHECK constraint), so the only database object that has to know the
new value is the stage function used by the lifecycle trigger. This replaces it with the migration 073 body plus
'wrong number dialed' in the dropped list. No rows are rewritten: nothing stores this value yet, and existing rows keep
their stage. Safe to rerun. Mirrors services/case_lifecycle.py (Tests/test_case_lifecycle.py checks they agree).
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
    IF k IN ('client side delay', 'dropped', 'wrong number dialed') THEN RETURN 'dropped'; END IF;
    IF k IN ('initial email sent (fee details shared)', 'initial email follow up', 'pc link shared', 'pc follow up',
             'initial email sent.', 'initial email followup', 'fee details shared & fu') THEN RETURN 'in_transition'; END IF;
    RETURN 'open';
END $$;
"""


def migrate_wrong_number_dialed():
    with engine.begin() as connection:
        connection.execute(text(STAGE_FUNCTION))
