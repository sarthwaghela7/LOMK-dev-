"""Create fd_dependencies table for tracking call follow-up questions and answers."""

from sqlalchemy import text

from storage.db import engine


def migrate_create_dependencies():
    """Create the dependencies table with appropriate indexes."""
    with engine.begin() as conn:
        # Create main table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS fd_dependencies (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                call_id TEXT,
                client_enquiry_date DATE,
                call_summary TEXT,
                question TEXT,
                agent_enquiry_date DATE,
                answer_by TEXT,
                answer_doc_link TEXT,
                answer_date DATE,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
            )
        """))
        
        # Create indexes for filtering and lookup
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fd_dependencies_call_id ON fd_dependencies(call_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fd_dependencies_client_enquiry_date ON fd_dependencies(client_enquiry_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fd_dependencies_answer_date ON fd_dependencies(answer_date)"))
        
        print("✓ Created fd_dependencies table with indexes")


if __name__ == "__main__":
    migrate_create_dependencies()
