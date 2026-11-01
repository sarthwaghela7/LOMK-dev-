"""Leads S2 "Case Status Change": taken from the Case Status Change saved on the lead's call record."""
from types import SimpleNamespace
from uuid import uuid4

from routes.flintdesk.leads import case_status_for_call


def _analysis(status):
    return SimpleNamespace(fd_m_case_status_change=status)


def test_a_lead_shows_the_status_saved_on_its_calls_record_trimmed():
    call = SimpleNamespace(call_id=uuid4())
    assert case_status_for_call(call, {str(call.call_id): _analysis("  Success - PC ")}) == "Success - PC"


def test_no_status_chosen_gives_an_empty_value_for_the_page_to_show_as_n_a():
    call = SimpleNamespace(call_id=uuid4())
    for saved in (None, "", "   "):
        assert case_status_for_call(call, {str(call.call_id): _analysis(saved)}) == ""


def test_no_call_or_no_analysis_record_gives_an_empty_value():
    call = SimpleNamespace(call_id=uuid4())
    assert case_status_for_call(None, {}) == ""
    assert case_status_for_call(call, {}) == ""
    assert case_status_for_call(call, {str(uuid4()): _analysis("Dropped")}) == ""       # another call's status is never borrowed


def test_the_value_is_read_each_time_so_a_later_change_on_the_calls_page_is_what_is_shown():
    call = SimpleNamespace(call_id=uuid4())
    records = {str(call.call_id): _analysis("Open")}
    assert case_status_for_call(call, records) == "Open"
    records[str(call.call_id)].fd_m_case_status_change = "Dropped"
    assert case_status_for_call(call, records) == "Dropped"
