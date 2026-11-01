"""
Clear ALL data from the database (all days, all tables).
Removes all calls, analysis results, and sync logs.

Usage:
    python clear_all_data.py --yes    # Confirm and clear
    python clear_all_data.py          # Show confirmation prompt
"""

from sqlalchemy import create_engine, text
from utils.config import DATABASE_URL

engine = create_engine(DATABASE_URL)

CONFIRM_FLAG = "--yes"


def _confirm():
    import sys
    if CONFIRM_FLAG not in sys.argv:
        print("=" * 70)
        print("⚠️  WARNING: This will DELETE ALL DATA from:")
        print("   • public.calls")
        print("   • public.call_analysis_results")
        print("   • public.sync_logs")
        print("=" * 70)
        print()
        response = input("Are you sure? Type 'yes' to confirm: ").strip().lower()
        if response != "yes":
            print("Cancelled.")
            sys.exit(0)
    else:
        print("=" * 70)
        print("Clearing all database tables...")
        print("=" * 70)


def _clear_all_tables():
    """
    Clear all tables using TRUNCATE CASCADE to respect foreign key constraints.
    Order matters: clear dependent tables first.
    """
    try:
        with engine.begin() as conn:
            # Disable foreign key constraints temporarily
            conn.execute(text("SET session_replication_role = 'replica'"))
            
            # Clear tables in order (dependent tables first)
            tables_to_clear = [
                "public.call_analysis_results",
                "public.calls",
                "public.sync_logs",
            ]
            
            for table in tables_to_clear:
                try:
                    conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
                    print(f"✓ Cleared {table}")
                except Exception as e:
                    print(f"⚠️  Could not clear {table}: {str(e)}")
            
            # Re-enable foreign key constraints
            conn.execute(text("SET session_replication_role = 'origin'"))
        
        print()
        print("=" * 70)
        print("✅ All data cleared successfully!")
        print("=" * 70)
        
    except Exception as e:
        print(f"❌ Error clearing tables: {str(e)}")
        raise


if __name__ == "__main__":
    _confirm()
    _clear_all_tables()
