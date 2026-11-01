from sqlalchemy import text, inspect
from storage.db import engine

inspector = inspect(engine)
tables = inspector.get_table_names()
print("Existing tables:", tables)

if 'prompts' in tables:
    inspector = inspect(engine)
    columns = inspector.get_columns('prompts')
    print("\nPrompts table exists!")
    print("Columns:", [col['name'] for col in columns])
    
    # Check content
    with engine.connect() as conn:
        rows = conn.execute(text('SELECT * FROM prompts')).fetchall()
        print(f"\nRows in prompts table: {len(rows)}")
        for row in rows:
            print(f"  {row}")
else:
    print("\nPrompts table does not exist yet")
