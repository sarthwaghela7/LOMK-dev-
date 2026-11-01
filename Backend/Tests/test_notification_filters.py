"""Notification bell: server-side All / Unread / Read, exact unread count, mark-all-read, recipient isolation.

Needs a DISPOSABLE local PostgreSQL database (never the production DATABASE_URL):
    NOTIFICATION_TEST_DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:55433/closure_test
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from routes.flintdesk import tasks


def test_routes_require_authentication():
    from middleware.auth import get_current_user
    wanted = {"/api/flintdesk/tasks/notifications", "/api/flintdesk/tasks/notifications/summary", "/api/flintdesk/tasks/notifications/read-all"}
    seen = set()
    for route in tasks.router.routes:
        if route.path in wanted:
            seen.add(route.path)
            assert get_current_user in [dep.call for dep in route.dependant.dependencies]
    assert seen == wanted


@pytest.fixture
def session():
    url = os.getenv("NOTIFICATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set NOTIFICATION_TEST_DATABASE_URL to a disposable local PostgreSQL database")
    assert make_url(url).host in {"localhost", "127.0.0.1"}
    schema = "test_nf_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema}"})
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE fd_users (id uuid primary key)"))
        conn.execute(text(
            "CREATE TABLE task_notifications (id uuid primary key, task_id uuid, lead_id uuid,"
            " recipient_id uuid not null references fd_users(id), message text not null, title text not null default 'N',"
            " notification_type text not null default 'x', source_page text, entity_id text, change_context jsonb,"
            " created_at timestamptz not null, read_at timestamptz,"
            " dismissed_at timestamptz, confirmed_at timestamptz, closure_id uuid)"))
    db = sessionmaker(bind=engine)()
    yield db
    db.close()
    engine.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def _seed(db, recipient, count, read):
    now = datetime.now(timezone.utc)
    for i in range(count):
        db.execute(text("INSERT INTO task_notifications (id, recipient_id, message, created_at, read_at) VALUES (:i,:r,:m,:c,:t)"),
                   {"i": uuid.uuid4(), "r": recipient, "m": f"{'read' if read else 'unread'}-{i}", "c": now - timedelta(minutes=i),
                    "t": now if read else None})
    db.commit()


def test_filters_counts_and_isolation(session):
    me, other = uuid.uuid4(), uuid.uuid4()
    for u in (me, other):
        session.execute(text("INSERT INTO fd_users VALUES (:u)"), {"u": u})
    _seed(session, me, 3, read=False)
    _seed(session, me, 2, read=True)
    _seed(session, other, 4, read=False)
    user = {"fd_user_id": str(me)}

    assert len(tasks.get_my_notifications(status="all", user=user, db=session)) == 5
    unread = tasks.get_my_notifications(status="unread", user=user, db=session)
    assert len(unread) == 3 and all(n["read_at"] is None for n in unread)
    read = tasks.get_my_notifications(status="read", user=user, db=session)
    assert len(read) == 2 and all(n["read_at"] for n in read)
    assert tasks.get_my_notifications(status="UNREAD ", user=user, db=session) == unread
    with pytest.raises(HTTPException) as bad:
        tasks.get_my_notifications(status="everything", user=user, db=session)
    assert bad.value.status_code == 422
    assert tasks.get_my_notification_summary(user=user, db=session) == {"unread": 3, "total": 5}

    # another user's notification can never be marked by me
    foreign = session.execute(text("SELECT id FROM task_notifications WHERE recipient_id=:o LIMIT 1"), {"o": other}).scalar()
    with pytest.raises(HTTPException) as nf:
        tasks.mark_notification_read(foreign, user=user, db=session)
    assert nf.value.status_code == 404

    mine = uuid.UUID(unread[0]["id"])
    tasks.mark_notification_read(mine, user=user, db=session)
    assert tasks.get_my_notification_summary(user=user, db=session)["unread"] == 2

    assert tasks.mark_all_notifications_read(user=user, db=session) == {"updated": 2}
    assert tasks.get_my_notification_summary(user=user, db=session) == {"unread": 0, "total": 5}
    assert tasks.get_my_notifications(status="unread", user=user, db=session) == []
    assert tasks.mark_all_notifications_read(user=user, db=session) == {"updated": 0}
    # the other user's notifications are untouched
    assert tasks.get_my_notification_summary(user={"fd_user_id": str(other)}, db=session) == {"unread": 4, "total": 4}


def test_list_is_capped_but_summary_is_exact(session):
    me = uuid.uuid4()
    session.execute(text("INSERT INTO fd_users VALUES (:u)"), {"u": me})
    _seed(session, me, 60, read=False)
    user = {"fd_user_id": str(me)}
    assert len(tasks.get_my_notifications(status="unread", user=user, db=session)) == 50
    assert tasks.get_my_notification_summary(user=user, db=session)["unread"] == 60
