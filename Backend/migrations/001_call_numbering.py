"""
Database migration: Create call_id formatting system (AF#1, AF#2, etc.)
"""
from sqlalchemy import text
from storage.db import engine

def migrate_call_numbering():
    """Create sequence and populate call_number for existing calls."""
    with engine.begin() as conn:
        # 1. Drop existing sequence if any
        conn.execute(text("DROP SEQUENCE IF EXISTS call_number_seq CASCADE"))
        
        # 2. Create new sequence starting from next number
        max_call_number = conn.execute(
            text("SELECT COALESCE(MAX(call_number), 0) FROM calls WHERE call_number IS NOT NULL")
        ).scalar()
        
        start_value = max(max_call_number + 1, 1)
        conn.execute(text(f"CREATE SEQUENCE call_number_seq START {start_value} INCREMENT 1"))
        print(f"✓ Created call_number_seq starting from {start_value}")
        
        # 3. Populate call_number for calls that don't have one
        conn.execute(text("""
            UPDATE calls 
            SET call_number = nextval('call_number_seq') 
            WHERE call_number IS NULL
        """))
        result = conn.execute(text("SELECT COUNT(*) FROM calls WHERE call_number IS NOT NULL")).scalar()
        print(f"✓ Populated call_number for {result} calls")
        
        # 4. Set DEFAULT for future inserts
        conn.execute(text("""
            ALTER TABLE calls 
            ALTER COLUMN call_number SET DEFAULT nextval('call_number_seq')
        """))
        print("✓ Set DEFAULT for call_number column")

if __name__ == "__main__":
    migrate_call_numbering()
    print("\n✓ Migration complete!")
