# Save as: clear_analysis.py
from storage.db import SessionLocal
from storage.models import CallAnalysisResult

def clear_analysis_table():
    db = SessionLocal()
    
    try:
        # Count before delete
        count = db.query(CallAnalysisResult).count()
        print(f"Current records in call_analysis_results: {count}")
        
        # Confirm
        confirm = input(f"Delete ALL {count} records? (yes/no): ")
        if confirm.lower() != "yes":
            print("Cancelled.")
            return
        
        # Delete all
        db.query(CallAnalysisResult).delete()
        db.commit()
        
        # Verify
        remaining = db.query(CallAnalysisResult).count()
        print(f"✓ Table cleared. Remaining: {remaining}")
        
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    clear_analysis_table()