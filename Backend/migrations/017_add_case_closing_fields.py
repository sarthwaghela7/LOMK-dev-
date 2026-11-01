"""
Migration 017: Add Case Closing fields for priority sorting and completion tracking.

Adds:
  - lead_score (Integer) — Paid Consultation lead score (0-100)
  - bos_score (Float) — Business Opportunity Score (0-10)
  - priority_tier (Text) — Computed priority tier (BOOK_NOW, GATHER_MORE_INFO, NURTURE, HIGH, HIGH_MID, STANDARD, N_A)
  - urgency_override (Text) — Urgency flag override (NOID, RFE, COURT_DATE, CLIENT_DISTRESSED, NONE)
  - case_closing_completed (Boolean) — Whether this case is marked as completed in the Case Closing workflow

These fields enable:
  1. Priority sorting on the Case Closing page
  2. Completion status tracking for the "Mark Completed" button
  3. Support for reference document logic (Lead Scoring Rubric, BOS Methodology)
"""

from sqlalchemy import text
from storage.db import engine

def migrate_add_case_closing_fields():
    """Add Case Closing fields to call_analysis_results table."""
    with engine.begin() as conn:
        # Check if columns already exist before adding
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='call_analysis_results' AND column_name='lead_score'
        """)).scalar()
        
        if result:
            print("✓ Case Closing fields already exist")
            return
        
        # Add new columns
        conn.execute(text("""
            ALTER TABLE call_analysis_results
            ADD COLUMN lead_score INTEGER,
            ADD COLUMN bos_score FLOAT,
            ADD COLUMN priority_tier TEXT,
            ADD COLUMN urgency_override TEXT DEFAULT 'NONE',
            ADD COLUMN case_closing_completed BOOLEAN DEFAULT false
        """))
        print("✓ Added lead_score, bos_score, priority_tier, urgency_override, case_closing_completed columns")
        
        # Create indexes
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_lead_score ON call_analysis_results(lead_score)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bos_score ON call_analysis_results(bos_score)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_priority_tier ON call_analysis_results(priority_tier)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_case_closing_completed ON call_analysis_results(case_closing_completed)"))
        print("✓ Created indexes for Case Closing fields")
