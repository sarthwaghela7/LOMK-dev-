from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from routes.flintdesk.tasks import _same_call_occurrence


def missed_call(number, when, call_id=None):
    return SimpleNamespace(
        task_type="missed_call",
        fd_m_tasks_contact_number=number,
        fd_m_tasks_missed_call_date=when,
        fd_m_tasks_miscall_added_date=None,
        fd_m_tasks_call_id=call_id,
    )


def test_same_client_number_on_a_new_date_is_a_new_assignable_call():
    previous = missed_call("7186406069", date(2026, 9, 22))
    today = missed_call("7186406069", date(2026, 9, 23))

    assert not _same_call_occurrence(previous, today)


def test_same_client_number_on_same_date_remains_one_call_assignment():
    first = missed_call("7186406069", date(2026, 9, 23))
    duplicate = missed_call("7186406069", date(2026, 9, 23))

    assert _same_call_occurrence(first, duplicate)


def test_exact_source_call_id_is_the_authoritative_call_identity():
    call_id = uuid4()
    first = missed_call("7186406069", date(2026, 9, 22), call_id)
    duplicate = missed_call("7186406069", date(2026, 9, 23), call_id)

    assert _same_call_occurrence(first, duplicate)
