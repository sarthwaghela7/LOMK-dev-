"""Build a DISPOSABLE local demo database for the Case Lifecycle feature (never production).

    createdb -h 127.0.0.1 -p 5432 lifecycle_demo
    DATABASE_URL=postgresql://user@127.0.0.1:5432/lifecycle_demo SECRET_KEY=local JWT_ALGORITHM=HS256 \
        python -m ops.local_lifecycle_demo

Creates the tables from the models, applies the closure and lifecycle migrations, seeds an admin login
(demo@local.test / demo-pass) and ~40 call records in the current month across all five lifecycle stages, including
five calls of ONE client and ONE context, then runs the Lost processor once. Refuses any non-local database host.
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from importlib import import_module

from sqlalchemy import make_url, text

from storage.db import SessionLocal, engine
from storage.models import Base
from utils.security import hash_password


def main():
    assert make_url(str(engine.url)).host in {"localhost", "127.0.0.1"}, "Local databases only"
    for table in Base.metadata.sorted_tables:       # a few unrelated models carry a literal uuid default PostgreSQL rejects
        try:
            with engine.begin() as conn:
                table.create(conn, checkfirst=True)
        except Exception:  # noqa: BLE001
            pass
    # Every release migration in order (each in its own transaction; one that cannot run on this scratch schema is skipped).
    from migrations.runner import MIGRATIONS
    for module_name, function_name in MIGRATIONS:
        try:
            getattr(import_module(module_name), function_name)()
        except Exception as exc:  # noqa: BLE001
            print(f"  skipped {module_name}: {type(exc).__name__}")

    today = date.today()
    first = today.replace(day=1)
    with SessionLocal() as db, db.begin():
        db.execute(text("""INSERT INTO fd_users (id, name, email, pass_hash, is_active, role, created_at)
            VALUES (:i, 'Demo Admin', 'demo@local.test', :h, true, 'admin', now()) ON CONFLICT (email) DO NOTHING"""),
                   {"i": uuid.uuid4(), "h": hash_password("demo-pass")})
        db.execute(text("""INSERT INTO clients (client_id, client_name, phone_number, first_call_date, last_call_date, created_at, updated_at)
            VALUES ('CL-DEMO-1', 'Jane Roe', '3125550100', :d, :d, now(), now()) ON CONFLICT DO NOTHING"""), {"d": first})
        plan = (
            # five calls: same client number AND same context, different statuses -> counted independently
            [("CL-DEMO-1", s, 0) for s in ("Open", "Open", "PC link shared", "PC link shared", "Success PC")]
            + [(None, "Open", 0)] * 6 + [(None, "Blocker", 0)] * 2 + [(None, "Same Client Context", 0)]
            + [(None, "Initial email sent (Fee Details shared)", 3), (None, "Initial Email Follow Up", 5), (None, "PC Follow Up", 7), (None, "PC link shared", 12)] * 2
            + [(None, "Dropped", 0)] * 3 + [(None, "Client Side Delay", 0)] * 2
            + [(None, "Success PC", 0)] * 3 + [(None, "Success GE", 0)] * 2
            + [(None, "PC link shared", 40)] * 12            # entered In-Transition 40 days ago -> Lost when processed
            + [(None, "PC Follow Up", 31)] * 2               # 31 days: the boundary day, NOT yet Lost
            + [(None, None, 0)] * 2
        )
        for index, (client, status, age_days) in enumerate(plan):
            call_id = uuid.uuid4()
            day = first + timedelta(days=index % max(1, (today - first).days + 1))
            seconds = 8 * 3600 + index * 97
            db.execute(text("""INSERT INTO calls (call_id, client_id, call_date, call_time, src, dst, billsec, disposition, call_reference)
                VALUES (:c, :cl, :d, :t, :s, '2243939098', 120, 'ANSWERED', :r)"""),
                       {"c": call_id, "cl": client, "d": day, "t": time(seconds // 3600, seconds % 3600 // 60, seconds % 60),
                        "s": "3125550100" if client else f"31255{index:05d}", "r": f"AF#{9000 + index}"})
            db.execute(text("""INSERT INTO call_analysis_results (call_id, fd_m_case_status_change, fd_a_case_type, call_recording_date_normalized,
                    fd_m_self_action_item, fd_m_manual_action_status, fd_m_dependency, fd_m_follow_up_dates, fd_m_action_due_date)
                VALUES (:c, :s, 'H-1B extension', :d, 'Follow up with client', 'Pending', 'Client', :d, :d)"""), {"c": str(call_id), "s": status, "d": day})
            if age_days:
                db.execute(text("""UPDATE call_analysis_results SET in_transition_entered_at = :t WHERE call_id = :c"""),
                           {"c": str(call_id), "t": datetime.now(timezone.utc) - timedelta(days=age_days)})
    from services import case_lost_processor as lost
    print("Lost processor:", lost.process_lost(SessionLocal))
    with SessionLocal() as db:
        print(dict(db.execute(text("SELECT case_lifecycle_stage, count(*) FROM call_analysis_results GROUP BY 1 ORDER BY 1")).all()))
        print("Teams batches waiting:", db.execute(text("SELECT count(*) FROM fd_case_lost_batches WHERE delivered_at IS NULL")).scalar())


if __name__ == "__main__":
    main()
