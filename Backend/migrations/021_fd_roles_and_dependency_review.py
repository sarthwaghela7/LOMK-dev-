"""Safely extend existing FlintDesk roles and dependency review fields."""

from sqlalchemy import text

from storage.db import engine


def migrate_fd_roles_and_dependency_review():
    with engine.begin() as conn:
        # fd_users.role is the existing TEXT role source; this is idempotent
        # and preserves all account credentials, emails, and assignments.
        conn.execute(text("ALTER TABLE fd_users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'agent'"))
        conn.execute(text("UPDATE fd_users SET role = 'agent' WHERE role IS NULL OR TRIM(role) = ''"))
        conn.execute(text("ALTER TABLE fd_dependencies ADD COLUMN IF NOT EXISTS agent_id UUID REFERENCES fd_users(id) ON DELETE SET NULL"))
        conn.execute(text("ALTER TABLE fd_dependencies ADD COLUMN IF NOT EXISTS agent_research_about_query TEXT"))
        conn.execute(text("ALTER TABLE fd_dependencies ADD COLUMN IF NOT EXISTS subject_matter_expert TEXT"))
        conn.execute(text("ALTER TABLE fd_dependencies ADD COLUMN IF NOT EXISTS subject_matter_expert_comment TEXT"))
        conn.execute(text("ALTER TABLE fd_dependencies ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'"))
        conn.execute(text("UPDATE fd_dependencies SET status = 'pending' WHERE status IS NULL OR TRIM(status) = ''"))
        # Preserve historical dependency answers under the new reviewer labels.
        conn.execute(text("UPDATE fd_dependencies SET subject_matter_expert = answer_by WHERE subject_matter_expert IS NULL AND answer_by IS NOT NULL"))
        conn.execute(text("UPDATE fd_dependencies SET subject_matter_expert_comment = answer_doc_link WHERE subject_matter_expert_comment IS NULL AND answer_doc_link IS NOT NULL"))
        conn.execute(text("""
            UPDATE fd_dependencies SET status = 'approved'
            WHERE status = 'pending' AND answer_by IS NOT NULL AND TRIM(answer_by) <> ''
              AND answer_doc_link IS NOT NULL AND TRIM(answer_doc_link) <> ''
              AND answer_date IS NOT NULL
        """))


if __name__ == "__main__":
    migrate_fd_roles_and_dependency_review()
