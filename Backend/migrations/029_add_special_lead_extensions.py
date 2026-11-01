from sqlalchemy import text
from storage.db import engine


SPECIAL_FALLBACKS = (("3128781255", "255"), ("2243374447", "4447"), ("601", "601"), ("603", "603"))


def migrate_add_special_lead_extensions():
    with engine.begin() as c:
        for parent_number, extension in SPECIAL_FALLBACKS:
            c.execute(text("""INSERT INTO fd_sync_filters (id, filter_type, value, parent_number, created_at, target_page)
                SELECT md5(random()::text || clock_timestamp()::text)::uuid, 'extension', :extension, :parent_number, now(), 'calls'
                WHERE NOT EXISTS (SELECT 1 FROM fd_sync_filters WHERE filter_type = 'extension' AND value = :extension AND parent_number = :parent_number)"""), {"extension": extension, "parent_number": parent_number})


if __name__ == '__main__':
    migrate_add_special_lead_extensions()
