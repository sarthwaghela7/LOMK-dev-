"""
Check current database status for debugging.
Shows count of records by processing_status and any mismatches.
"""
from storage.db import SessionLocal
from storage.models import Call, CallAnalysisResult

db = SessionLocal()

# Check calls table status distribution
print("\n" + "="*80)
print("CALLS TABLE - Processing Status Distribution")
print("="*80)

status_counts = {}
all_calls = db.query(Call).all()

for call in all_calls:
    status = call.processing_status or "NULL"
    status_counts[status] = status_counts.get(status, 0) + 1

for status, count in sorted(status_counts.items()):
    print(f"  {status:20s}: {count:4d} records")

print(f"\n  Total calls: {len(all_calls)}")

# Show details of unprocessed calls
print("\n" + "="*80)
print("UNPROCESSED CALLS - Details")
print("="*80)

unprocessed = db.query(Call).filter(Call.processing_status == "not processed").all()
print(f"\nRecords with status='not processed': {len(unprocessed)}")
if unprocessed:
    for call in unprocessed[:5]:
        print(f"  - {call.call_id} | {call.call_date} {call.call_time} | Audio: {bool(call.audio_s3_uri)}")
    if len(unprocessed) > 5:
        print(f"  ... and {len(unprocessed) - 5} more")

# Check call_analysis_results
print("\n" + "="*80)
print("CALL_ANALYSIS_RESULTS TABLE")
print("="*80)

analysis_count = db.query(CallAnalysisResult).count()
print(f"\nTotal analysis records: {analysis_count}")

if analysis_count > 0:
    recent = db.query(CallAnalysisResult).limit(3).all()
    print("\nRecent analysis records:")
    for record in recent:
        print(f"  - Call ID: {record.call_id}")
        print(f"    Created: {record.created_at}")
        print(f"    Score: {record.overall_call_quality_score}")
        print()

print("="*80)
db.close()
