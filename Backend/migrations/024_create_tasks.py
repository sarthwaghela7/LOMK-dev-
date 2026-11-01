from sqlalchemy import text
from storage.db import engine

def migrate_create_tasks():
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS tasks (id UUID PRIMARY KEY, task_type TEXT NOT NULL CHECK(task_type IN ('general','missed_call','non_responsiveness')), fd_m_tasks_date DATE, fd_m_tasks_tasks TEXT, fd_m_tasks_timeline TEXT, fd_m_tasks_owner UUID REFERENCES fd_users(id), fd_m_tasks_status TEXT, fd_m_tasks_comments TEXT, fd_m_tasks_missed_call_date DATE, fd_m_tasks_miscall_added_date DATE, fd_m_tasks_name TEXT, fd_m_tasks_contact_number TEXT, fd_m_tasks_summary TEXT, fd_m_tasks_action_status TEXT, fd_m_tasks_date_added DATE, fd_m_tasks_type TEXT, fd_m_tasks_phone_number TEXT, fd_m_tasks_email TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), submitted_at TIMESTAMPTZ)"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS task_notifications (id UUID PRIMARY KEY, task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, recipient_id UUID NOT NULL REFERENCES fd_users(id), message TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), read_at TIMESTAMPTZ)"""))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_tasks_type_date ON tasks(task_type, fd_m_tasks_date)"))

if __name__ == '__main__': migrate_create_tasks()
