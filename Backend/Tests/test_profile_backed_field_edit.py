"""The Calls table shows a client's name and email from the client profile, so an
edit of those fields must be checked against, and written to, the profile.

Before: the stale-write check compared what the table showed (the profile value)
with the call's own stored copy, so once the profile was filled or changed every
edit of that field on the client's calls failed as "updated by another user"
(and an edit that did pass was invisible, because the profile value is shown).
"""
import contextlib
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from routes.flintdesk.calls import FlintDeskSingleFieldUpdateRequest, update_flintdesk_single_field
from storage.models import CallAnalysisResult, Client

AGENT = {"role": "agent", "fd_user_id": "agent-1", "name": "Shrishti"}


class _Query:
    def __init__(self, result):
        self._result = result

    def filter(self, *_a, **_k):
        return self

    def first(self):
        return self._result


class _DB:
    def __init__(self, analysis, profile=None):
        self.analysis, self.profile, self.added = analysis, profile, []

    def query(self, model):
        return _Query(self.analysis if model is CallAnalysisResult else self.profile if model is Client else None)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def flush(self):
        pass


@contextlib.contextmanager
def _wire(analysis, client_id="CL-1"):
    call = SimpleNamespace(call_id=analysis.call_id, client=None, client_id=client_id, fd_processing_status=None)
    with patch("routes.flintdesk.calls._get_call_or_404", return_value=call), \
         patch("routes.flintdesk.calls._get_or_create_analysis_record", return_value=analysis), \
         patch("routes.flintdesk.calls._assert_call_access"), \
         patch("routes.flintdesk.calls._assert_calls_page_edit_access"):
        yield


def _analysis(**kw):
    return CallAnalysisResult(id=1, call_id=str(uuid4()), **kw)


def _profile(**kw):
    return SimpleNamespace(client_id="CL-1", client_name=kw.get("name"), client_email=kw.get("email"), updated_at=None)


def _edit(analysis, db, value, expected, field="fd_a_email_address", client_id="CL-1"):
    with _wire(analysis, client_id):
        return update_flintdesk_single_field(
            call_id=str(analysis.call_id),
            payload=FlintDeskSingleFieldUpdateRequest(field=field, value=value, expected_value=expected),
            user=AGENT, db=db,
        )


def test_edit_is_checked_against_the_profile_value_the_table_shows_and_updates_it():
    analysis = _analysis(fd_a_email_address=None)          # this call never stored an email
    profile = _profile(email="old@example.com")            # but the profile has one, and the table shows it
    response = _edit(analysis, _DB(analysis, profile), "new@example.com", expected="old@example.com")
    assert analysis.fd_a_email_address == "new@example.com"
    assert profile.client_email == "new@example.com"       # what the agent typed is what is shown next
    assert response["client"]["client_email"] == "new@example.com"


def test_an_agent_can_update_a_value_again_after_filling_it_once():
    analysis = _analysis(fd_a_email_address="stale-ai-guess@example.com")
    profile = _profile(email="first@example.com")
    db = _DB(analysis, profile)
    _edit(analysis, db, "second@example.com", expected="first@example.com")
    _edit(analysis, db, "third@example.com", expected="second@example.com")
    assert profile.client_email == "third@example.com" and analysis.fd_a_email_address == "third@example.com"


def test_a_genuinely_stale_edit_is_still_refused():
    analysis = _analysis(fd_a_email_address=None)
    profile = _profile(email="changed-by-someone-else@example.com")
    with pytest.raises(HTTPException) as raised:
        _edit(analysis, _DB(analysis, profile), "mine@example.com", expected="what-i-saw@example.com")
    assert raised.value.status_code == 409
    assert profile.client_email == "changed-by-someone-else@example.com"


def test_name_edit_updates_the_profile_name():
    analysis = _analysis(fd_a_client_name=None)
    profile = _profile(name="Old Name")
    _edit(analysis, _DB(analysis, profile), "New Name", expected="Old Name", field="fd_a_client_name")
    assert profile.client_name == "New Name" and analysis.fd_a_client_name == "New Name"


def test_without_a_profile_value_the_call_s_own_value_is_used_as_before():
    analysis = _analysis(fd_a_email_address="own@example.com")
    for profile in (None, _profile(email=None)):
        response = _edit(analysis, _DB(analysis, profile), "next@example.com", expected=analysis.fd_a_email_address)
        assert analysis.fd_a_email_address == "next@example.com"
    # and no client_id on the call: no profile involved at all
    solo = _analysis(fd_a_email_address="a@example.com")
    assert _edit(solo, _DB(solo), "b@example.com", expected="a@example.com", client_id=None)["client"] is None


def test_other_fields_are_unaffected_by_the_profile():
    analysis = _analysis(fd_a_case_type="Family")
    profile = _profile(email="x@example.com")
    _edit(analysis, _DB(analysis, profile), "Divorce", expected="Family", field="fd_a_case_type")
    assert analysis.fd_a_case_type == "Divorce" and profile.client_email == "x@example.com"
