"""Guards for "an agent who filled a field on their own call can update it again".

1. Access: outbound calls are editable by the agent the Agent column shows as
   owner (assignment in effect on the call's date), not only by whoever holds
   the extension today; inbound calls stay editable by every agent.
2. Display overrides: the stale-write check compares what the table showed with
   what is stored. Any editable field the API displays from somewhere other
   than the call's own column must be registered, or edits of it are refused as
   "updated by another user" forever. This test fails when a new override is
   added without being registered.
"""
import inspect
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

import routes.calls as calls_routes
from routes.flintdesk.calls import FAST_EDITABLE_FIELDS, PROFILE_BACKED_FIELDS
from services.call_access import assert_manual_call_access


def _outbound_call():
    return SimpleNamespace(call_id=uuid4(), call_date=__import__("datetime").date(2026, 10, 7), extension="102")


def _agent():
    return {"role": "agent", "fd_user_id": str(uuid4())}


def _db_without_active_assignment():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


def test_the_agent_shown_on_the_call_can_still_edit_it_after_the_extension_moves():
    call, user = _outbound_call(), _agent()
    shown_owner = {str(call.call_id): (user["fd_user_id"], "Shrishti")}
    with patch("services.call_access.is_inbound_call", return_value=False), \
         patch("routes.calls.assignment_agent_details", return_value=shown_owner):
        assert_manual_call_access(call, user, _db_without_active_assignment())  # no exception


def test_another_agents_outbound_call_is_still_refused():
    call, user = _outbound_call(), _agent()
    someone_else = {str(call.call_id): (str(uuid4()), "Gokul")}
    with patch("services.call_access.is_inbound_call", return_value=False), \
         patch("routes.calls.assignment_agent_details", return_value=someone_else):
        with pytest.raises(HTTPException) as raised:
            assert_manual_call_access(call, user, _db_without_active_assignment())
    assert raised.value.status_code == 403


def test_a_call_with_no_owner_is_refused_for_agents():
    call, user = _outbound_call(), _agent()
    with patch("services.call_access.is_inbound_call", return_value=False), \
         patch("routes.calls.assignment_agent_details", return_value={}):
        with pytest.raises(HTTPException):
            assert_manual_call_access(call, user, _db_without_active_assignment())


def test_every_agent_can_edit_inbound_calls():
    with patch("services.call_access.is_inbound_call", return_value=True):
        assert_manual_call_access(_outbound_call(), _agent(), MagicMock())


def test_editable_fields_shown_from_another_source_are_registered():
    """New display override of an editable field => register it in PROFILE_BACKED_FIELDS (or handle it like the agent name)."""
    source = inspect.getsource(calls_routes.serialize_analysis_rows)
    overridden = {key for key in re.findall(r'data\["(\w+)"\]\s*=(?!=)', source) if key in FAST_EDITABLE_FIELDS}
    registered = set(PROFILE_BACKED_FIELDS) | {"front_desk_person_name"}  # agent name is edited from its raw value (raw_front_desk_person_name)
    assert overridden <= registered, (
        f"{sorted(overridden - registered)} are shown from a different source than the call's own column, "
        "so agents' edits would be refused as 'updated by another user'. Register them in "
        "routes.flintdesk.calls.PROFILE_BACKED_FIELDS and make the save check/write that source."
    )
