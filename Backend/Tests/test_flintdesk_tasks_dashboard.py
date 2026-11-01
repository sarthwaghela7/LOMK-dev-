from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from storage.models import Call, FDTask
from routes.flintdesk.tasks import dashboard_summary


def test_dashboard_summary_missed_call_uses_callback_match_not_stale_action_status():
    """Regression test for the bug reported against the Dashboard: "# Called
    Back" for Missed Calls always showed 0 because it trusted
    fd_m_tasks_action_status, a field nothing writes to for missed_call
    tasks any more. It must instead use the same phone+date call-matching
    (_callback_match) the Missed Calls page's own Called Back view uses.
    """
    matched_task = SimpleNamespace(
        fd_m_tasks_missed_call_date=date(2026, 9, 1),
        fd_m_tasks_miscall_added_date=date(2026, 9, 1),
        fd_m_tasks_contact_number="3125550100",
        fd_m_tasks_action_status=None,
        task_type="missed_call",
    )
    # Stale/impossible data on purpose: action_status says "done" but there
    # is no real matching outbound call for this number. The fix must not
    # trust this field at all any more.
    falsely_marked_done_task = SimpleNamespace(
        fd_m_tasks_missed_call_date=date(2026, 9, 1),
        fd_m_tasks_miscall_added_date=date(2026, 9, 1),
        fd_m_tasks_contact_number="3125550199",
        fd_m_tasks_action_status="done",
        task_type="missed_call",
    )
    outbound_call = SimpleNamespace(src="3125550100", dst="602")

    db = MagicMock()

    def query_side_effect(*entities):
        query = MagicMock()
        # `is`, never `==` -- SQLAlchemy column/label expressions (the
        # non_responsiveness query below passes those) overload `==` to
        # build a SQL comparison instead of a normal equality check.
        if len(entities) == 1 and entities[0] is FDTask:
            query.filter.return_value.all.return_value = [matched_task, falsely_marked_done_task]
        elif len(entities) == 1 and entities[0] is Call:
            # dashboard_summary now scopes this query to the missed-call
            # tasks' own phone numbers in SQL (.filter(...).all()) instead
            # of fetching every call.
            query.filter.return_value.all.return_value = [outbound_call]
        else:
            query.filter.return_value.group_by.return_value.all.return_value = []
        return query

    db.query.side_effect = query_side_effect

    with patch("routes.flintdesk.tasks._system_numbers", return_value=set()), \
         patch("routes.flintdesk.tasks._callback_match", side_effect=lambda calls, task_date, system_numbers: bool(calls)):
        result = dashboard_summary(date(2026, 9, 1), date(2026, 9, 1), "", {"role": "admin"}, db)

    row = result["missed_calls"][0]
    assert row["count"] == 2
    # Only the phone-matched task counts, regardless of the other task's
    # stale action_status value.
    assert row["called_back"] == 1
    assert result["totals"]["missed_called_back"] == 1


def test_dashboard_summary_missed_call_with_no_matching_calls_reports_zero_called_back():
    task = SimpleNamespace(
        fd_m_tasks_missed_call_date=date(2026, 9, 1),
        fd_m_tasks_miscall_added_date=date(2026, 9, 1),
        fd_m_tasks_contact_number="3125550100",
        fd_m_tasks_action_status=None,
        task_type="missed_call",
    )
    db = MagicMock()

    def query_side_effect(*entities):
        query = MagicMock()
        if len(entities) == 1 and entities[0] is FDTask:
            query.filter.return_value.all.return_value = [task]
        elif len(entities) == 1 and entities[0] is Call:
            query.all.return_value = []
        else:
            query.filter.return_value.group_by.return_value.all.return_value = []
        return query

    db.query.side_effect = query_side_effect

    with patch("routes.flintdesk.tasks._system_numbers", return_value=set()):
        result = dashboard_summary(date(2026, 9, 1), date(2026, 9, 1), "", {"role": "admin"}, db)

    row = result["missed_calls"][0]
    assert row["count"] == 1
    assert row["called_back"] == 0
