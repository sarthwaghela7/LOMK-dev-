"""Persist each auto-created Missed Call task's Hannah AI category.

services/missed_call_sync.py now labels every "missed_call" task it
creates/enriches from a real Call row as either "front_desk_agent" or
"website" (see services/hannah_ai_routing.py) -- computed once at sync time
from the call session's ring-route legs, not re-derived on every page load.
NULL for manually imported rows and for any call session that doesn't have
a qualifying route pair (see hannah_ai_routing.category_for_dst_set).
"""
from sqlalchemy import text

from storage.db import engine


def migrate_add_hannah_ai_category_to_tasks():
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS fd_m_tasks_hannah_ai_category TEXT"))


if __name__ == "__main__":
    migrate_add_hannah_ai_category_to_tasks()
