"""
Verification script to check that call_id and call_reference are properly
stored in both calls and call_analysis_results tables.

Usage:
    python -m Tests.tools.verify_call_mapping
"""

from storage.db import SessionLocal
from storage.models import Call, CallAnalysisResult
from datetime import datetime

def verify_call_mapping():
    """Verify call_id and call_reference mapping between tables."""
    
    db = SessionLocal()
    
    try:
        print("\n" + "="*80)
        print("CALL MAPPING VERIFICATION")
        print("="*80)
        
        # Count records in each table
        total_calls = db.query(Call).count()
        total_analysis = db.query(CallAnalysisResult).count()
        
        print(f"\nTable Statistics:")
        print(f"  - calls table: {total_calls} records")
        print(f"  - call_analysis_results table: {total_analysis} records")
        
        # Check for calls with proper call_id and call_reference
        print(f"\nCalls Table Sample (first 5 processed calls):")
        processed_calls = db.query(Call).filter(
            Call.processing_status == "processed"
        ).limit(5).all()
        
        if processed_calls:
            for idx, call in enumerate(processed_calls, 1):
                print(f"\n  [{idx}] ID: {call.call_id}")
                print(f"      Reference: {call.call_reference}")
                print(f"      Number: {call.call_number}")
                print(f"      Date/Time: {call.call_date} {call.call_time}")
                print(f"      Status: {call.processing_status}")
        else:
            print("  (No processed calls found)")
        
        # Check analysis records and their mapping
        print(f"\n\nCall Analysis Results Sample (first 5 records):")
        analysis_records = db.query(CallAnalysisResult).limit(5).all()
        
        if analysis_records:
            for idx, record in enumerate(analysis_records, 1):
                print(f"\n  [{idx}] Analysis ID: {record.id}")
                print(f"      call_id: {record.call_id}")
                print(f"      call_reference: {record.call_reference}")
                print(f"      call_number: {record.call_number}")
                print(f"      Created: {record.created_at}")
                
                # Verify mapping - check if this call exists in calls table
                source_call = db.query(Call).filter(
                    Call.call_id == record.call_id
                ).first()
                
                if source_call:
                    match = (source_call.call_reference == record.call_reference and
                            source_call.call_number == record.call_number)
                    status = "✓ MATCH" if match else "✗ MISMATCH"
                    print(f"      {status} - Found in calls table")
                else:
                    print(f"      ✗ NOT FOUND in calls table")
        else:
            print("  (No analysis records found)")
        
        # Statistics on mapping completeness
        print(f"\n\nMapping Completeness Check:")
        
        # Check analysis records with missing call_reference
        missing_ref = db.query(CallAnalysisResult).filter(
            CallAnalysisResult.call_reference.is_(None)
        ).count()
        print(f"  - Analysis records with missing call_reference: {missing_ref}")
        
        # Check analysis records with missing call_number
        missing_num = db.query(CallAnalysisResult).filter(
            CallAnalysisResult.call_number.is_(None)
        ).count()
        print(f"  - Analysis records with missing call_number: {missing_num}")
        
        # Check calls table status distribution
        print(f"\n\nCalls Table Status Distribution:")
        status_query = db.query(
            Call.processing_status,
            db.func.count(Call.call_id).label('count')
        ).group_by(Call.processing_status).all()
        
        for status, count in status_query:
            print(f"  - {status or 'NULL':20s}: {count:4d} records")
        
        print("\n" + "="*80)
        
    except Exception as e:
        print(f"\n✗ Verification failed: {type(e).__name__}: {e}")
        print("="*80)
        return False
    
    finally:
        db.close()
    
    return True

if __name__ == "__main__":
    verify_call_mapping()
