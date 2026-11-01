from sqlalchemy import create_engine, text
from utils.config import DATABASE_URL

engine = create_engine(DATABASE_URL)

CONFIRM_FLAG = "--yes"


def _confirm():
    import sys
    if CONFIRM_FLAG not in sys.argv:
        print("This will DELETE ALL ROWS from public.calls and public.sync_logs.")
        print("Run with '--yes' to proceed.")
        sys.exit(1)


def _clear_tables():
    # Use TRUNCATE CASCADE to ensure dependent rows are removed if necessary.
    stmt = text(
        "TRUNCATE TABLE public.calls, public.sync_logs RESTART IDENTITY CASCADE"
    )
    with engine.begin() as conn:
        conn.execute(stmt)


if __name__ == "__main__":
    _confirm()
    _clear_tables()
    print("Cleared tables: public.calls, public.sync_logs")
