from sqlalchemy import create_engine, text

from utils.config import DATABASE_URL

engine = create_engine(DATABASE_URL)

# Only inspect these specific tables
TABLE_NAMES = [
    'call_analysis_results',
    'calls',
    'sync_logs',
    'users',
]

query = """
SELECT
    table_schema,
    table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = ANY(:table_names)
ORDER BY table_schema, table_name, ordinal_position;
"""

with engine.connect() as conn:
    result = conn.execute(text(query), {"table_names": TABLE_NAMES})

    current_schema = None
    current_table = None

    for row in result:
        if current_schema != row.table_schema or current_table != row.table_name:
            current_schema = row.table_schema
            current_table = row.table_name

            print("\n" + "=" * 80)
            print(f"TABLE: {row.table_schema}.{row.table_name}")
            print("=" * 80)

        print(
            f"{row.column_name:<30}"
            f"{row.data_type:<20}"
            f"{row.is_nullable}"
        )