from sqlalchemy import text, inspect
from storage.db import engine

# Get calls table schema
inspector = inspect(engine)
calls_cols = inspector.get_columns('calls')
print("=== CALLS TABLE ===")
for col in calls_cols:
    print(f"  {col['name']:30} {col['type']}")

print("\n=== CALL_ANALYSIS_RESULTS TABLE ===")
analysis_cols = inspector.get_columns('call_analysis_results')
for col in analysis_cols:
    print(f"  {col['name']:30} {col['type']}")

# Check some sample data
print("\n=== SAMPLE CALLS DATA ===")
with engine.connect() as conn:
    rows = conn.execute(text('SELECT id, call_id FROM calls LIMIT 3')).fetchall()
    for row in rows:
        print(f"  id={row[0]}, call_id={row[1]}")
