import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.call_access import assert_completion_access, assert_manual_call_access
from routes.flintdesk.calls import FlintDeskManualFieldUpdateRequest
from routes.flintdesk.calls import _assert_agent_self_identification
from services.call_access import is_inbound_call


def test_inbound_allows_unassigned_authenticated_agent():
    db = MagicMock()
    with patch("services.call_access.is_inbound_call", return_value=True):
        assert_manual_call_access(SimpleNamespace(extension="603"),
                                  {"role": "agent", "fd_user_id": str(uuid4())}, db)
    db.query.assert_not_called()


def test_inbound_does_not_bypass_authenticated_identity():
    with patch("services.call_access.is_inbound_call", return_value=True):
        with pytest.raises(HTTPException) as raised:
            assert_manual_call_access(SimpleNamespace(extension="603"), {}, MagicMock())
    assert raised.value.status_code == 403


@pytest.mark.parametrize("inbound", [True, False])
def test_agent_name_changes_are_shared_only_on_inbound(inbound):
    with patch("routes.flintdesk.calls.is_inbound_call", return_value=inbound):
        args = ("front_desk_person_name", "Another Agent", {"role": "agent", "name": "Me"})
        if inbound:
            _assert_agent_self_identification(*args, call=object(), db=MagicMock())
        else:
            with pytest.raises(HTTPException) as raised:
                _assert_agent_self_identification(*args, call=object(), db=MagicMock())
            assert raised.value.status_code == 403


@pytest.mark.parametrize("src,dst,expected", [
    ("3125550123", "2243374447", True),
    ("2243374447", "3125550123", False),
    ("3125550123", "603", True),
    ("3125550123", "999", False),
])
def test_inbound_permission_uses_stored_routing(src, dst, expected):
    with patch("routes.calls._system_numbers", return_value={"2243374447", "603"}):
        assert is_inbound_call(SimpleNamespace(src=src, dst=dst), MagicMock()) is expected


def test_active_extension_assignment_allows_agent_manual_edits():
    agent_id = uuid4()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = object()

    assert_manual_call_access(
        SimpleNamespace(extension="101"),
        {"role": "agent", "fd_user_id": str(agent_id)},
        db,
    )


def test_active_extension_assignment_allows_agent_completion():
    agent_id = uuid4()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = object()

    assert_completion_access(
        SimpleNamespace(extension="101"),
        {"role": "agent", "fd_user_id": str(agent_id)},
        db,
    )


def test_inactive_or_missing_assignment_rejects_agent_manual_edits():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(HTTPException) as raised:
        assert_manual_call_access(
            SimpleNamespace(extension="101"),
            {"role": "agent", "fd_user_id": str(uuid4())},
            db,
        )

    assert raised.value.status_code == 403


def test_privileged_role_does_not_need_extension_assignment():
    db = MagicMock()

    assert_manual_call_access(SimpleNamespace(extension="101"), {"role": "team lead"}, db)

    db.query.assert_not_called()


def test_manual_field_request_allows_agent_stage_1_corrections():
    payload = FlintDeskManualFieldUpdateRequest(fd_a_lead_source="Website")

    assert payload.fd_a_lead_source == "Website"
