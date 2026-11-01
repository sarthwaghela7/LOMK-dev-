"""Move extensions 203–205 to lead number 2243939098.

The extension rows are updated in place, so each extension has exactly one
current sync mapping. Current agent ownership is preserved: an already-present
same-day assignment is moved to the new lead number, while a prior-day active
assignment is closed and copied forward as an effective-today assignment.
"""

from sqlalchemy import bindparam, text

from storage.db import engine


NEW_NUMBER = "2243939098"
EXTENSIONS = ("203", "204", "205")


def migrate_move_website_extensions_to_2243939098():
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO fd_sync_filters
                    (id, filter_type, value, parent_number, created_at, target_page)
                SELECT md5(random()::text || clock_timestamp()::text)::uuid,
                       'number', :number, NULL, now(), NULL
                WHERE NOT EXISTS (
                    SELECT 1 FROM fd_sync_filters
                    WHERE filter_type = 'number' AND value = :number
                )
                """
            ),
            {"number": NEW_NUMBER},
        )
        connection.execute(
            text(
                """
                UPDATE fd_sync_filters
                SET parent_number = :number
                WHERE filter_type = 'extension'
                  AND value IN :extensions
                """
            ).bindparams(bindparam("extensions", expanding=True)),
            {"number": NEW_NUMBER, "extensions": EXTENSIONS},
        )

        # Close prior-day active assignments, then carry each current owner's
        # assignment to the new lead number for the Central date of release.
        # A same-day row must be updated in place because the schema permits
        # only one assignment per extension per day.
        connection.execute(
            text(
                """
                UPDATE fd_assignments
                SET is_active = FALSE, expires_at = now()
                WHERE extension IN :extensions AND is_active IS TRUE
                """
            ).bindparams(bindparam("extensions", expanding=True)),
            {"extensions": EXTENSIONS},
        )
        connection.execute(
            text(
                """
                UPDATE fd_assignments
                SET number = :number, is_active = TRUE, expires_at = NULL,
                    created_at = now()
                WHERE extension IN :extensions
                  AND assignment_date = (now() AT TIME ZONE 'America/Chicago')::date
                """
            ).bindparams(bindparam("extensions", expanding=True)),
            {"number": NEW_NUMBER, "extensions": EXTENSIONS},
        )
        connection.execute(
            text(
                """
                INSERT INTO fd_assignments
                    (id, assignment_date, extension, number, agent_id, created_at,
                     expires_at, is_active)
                SELECT md5(random()::text || clock_timestamp()::text || source.extension)::uuid,
                       (now() AT TIME ZONE 'America/Chicago')::date,
                       source.extension, :number, source.agent_id, now(), NULL, TRUE
                FROM (
                    SELECT DISTINCT ON (extension) extension, agent_id
                    FROM fd_assignments
                    WHERE extension IN :extensions
                    ORDER BY extension, assignment_date DESC, created_at DESC, id DESC
                ) AS source
                WHERE NOT EXISTS (
                    SELECT 1 FROM fd_assignments today
                    WHERE today.extension = source.extension
                      AND today.assignment_date = (now() AT TIME ZONE 'America/Chicago')::date
                )
                """
            ).bindparams(bindparam("extensions", expanding=True)),
            {"number": NEW_NUMBER, "extensions": EXTENSIONS},
        )


if __name__ == "__main__":
    migrate_move_website_extensions_to_2243939098()
