"""
Sync call_number from calls table to call_analysis_results table based on call_id
"""
import sys
sys.path.insert(0, '.')
from storage.db import engine
from sqlalchemy import text

with engine.begin() as conn:
    # Update call_analysis_results.call_number from calls.call_number
    # Match on the UUID call_id
    update_sql = """
        UPDATE call_analysis_results car
        SET call_number = c.call_number
        FROM calls c
        WHERE c.call_id::TEXT = car.call_id
    """
    conn.execute(text(update_sql))
    result = conn.execute(text('SELECT COUNT(*) FROM call_analysis_results WHERE call_number IS NOT NULL')).scalar()
    print(f'Synced call_number for {result} analysis records')
