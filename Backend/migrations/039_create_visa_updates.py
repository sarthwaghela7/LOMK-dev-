from sqlalchemy import text

from storage.db import engine


def migrate_create_visa_updates():
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS visa_updates (
                id UUID PRIMARY KEY,
                case_type TEXT NOT NULL CHECK (length(trim(case_type)) > 0),
                title TEXT NOT NULL CHECK (length(trim(title)) > 0),
                details TEXT NOT NULL CHECK (length(trim(details)) > 0),
                reference_link TEXT,
                update_date DATE NOT NULL,
                status TEXT NOT NULL DEFAULT 'published' CHECK (status = 'published'),
                created_by UUID NOT NULL REFERENCES fd_users(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS visa_update_questions (
                id UUID PRIMARY KEY,
                update_id UUID NOT NULL REFERENCES visa_updates(id) ON DELETE CASCADE,
                question TEXT NOT NULL CHECK (length(trim(question)) > 0),
                question_type TEXT NOT NULL CHECK (question_type IN ('single_select', 'multiple_select', 'short_answer', 'true_false')),
                options JSONB,
                correct_answer JSONB,
                required BOOLEAN NOT NULL DEFAULT TRUE,
                position INTEGER NOT NULL DEFAULT 0
            )
        """))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS visa_update_acknowledgements (
                id UUID PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES fd_users(id) ON DELETE CASCADE,
                update_id UUID NOT NULL REFERENCES visa_updates(id) ON DELETE CASCADE,
                read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_visa_update_ack_user_update UNIQUE (user_id, update_id)
            )
        """))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS visa_update_responses (
                id UUID PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES fd_users(id) ON DELETE CASCADE,
                update_id UUID NOT NULL REFERENCES visa_updates(id) ON DELETE CASCADE,
                question_id UUID NOT NULL REFERENCES visa_update_questions(id) ON DELETE CASCADE,
                answer JSONB NOT NULL,
                submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_visa_response_user_question UNIQUE (user_id, question_id)
            )
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_visa_updates_date ON visa_updates(update_date DESC, created_at DESC)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_visa_update_questions_update ON visa_update_questions(update_id, position)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_visa_update_ack_user ON visa_update_acknowledgements(user_id, update_id)"))


if __name__ == "__main__":
    migrate_create_visa_updates()
