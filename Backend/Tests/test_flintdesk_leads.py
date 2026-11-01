from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from routes.flintdesk.leads import (
    LeadBulkDelete,
    _assigned_extensions,
    _outbound_calls_by_lead,
    _phone,
    _set_owner,
    delete_leads,
)


def test_admin_scope_is_unrestricted():
    assert _assigned_extensions(MagicMock(), {"role": "admin"}) is None


def test_user_scope_comes_only_from_active_assignments():
    query = MagicMock()
    query.filter.return_value.all.return_value = [("101",), (" 202 ",)]
    db = MagicMock()
    db.query.return_value = query
    result = _assigned_extensions(db, {"role": "team_lead", "fd_user_id": str(uuid4())})
    assert result == {"101", "202"}
    query.filter.assert_called_once()


def test_user_without_valid_identity_has_empty_scope():
    assert _assigned_extensions(MagicMock(), {"role": "agent"}) == set()


def test_phone_matching_normalizes_country_code_and_formatting():
    assert _phone("+1 (312) 555-0199") == "3125550199"
    assert _phone("312-555-0199") == "3125550199"


def test_owner_assignment_keeps_display_name_but_persists_canonical_id():
    agent = SimpleNamespace(id=uuid4(), name="Shrishti")
    record = {}
    _set_owner(record, agent)
    assert record == {"owner": "Shrishti", "owner_id": agent.id}


def test_admin_bulk_deletion_removes_only_selected_canonical_leads():
    selected_ids = [uuid4(), uuid4()]
    rows = [SimpleNamespace(id=lead_id) for lead_id in selected_ids]
    db = MagicMock()
    db.get.return_value = None
    db.query.return_value.filter.return_value.all.return_value = rows

    result = delete_leads(LeadBulkDelete(ids=selected_ids), {"role": "admin"}, db)

    assert result == {"deleted": 2}
    assert db.delete.call_args_list == [((rows[0],),), ((rows[1],),)]
    db.commit.assert_called_once()


def test_agent_cannot_invoke_bulk_lead_deletion():
    db = MagicMock()
    db.get.return_value = None

    with pytest.raises(HTTPException) as error:
        delete_leads(LeadBulkDelete(ids=[uuid4()]), {"role": "agent"}, db)

    assert error.value.status_code == 403
    db.delete.assert_not_called()


# --- Dashboard "Website & Paid Ads": tracks outbound calls agents placed to
# leads, matched by phone + date -- see _outbound_calls_by_lead. -----------


def test_outbound_calls_by_lead_requires_outbound_direction_not_just_same_number():
    lead = SimpleNamespace(
        id=uuid4(),
        lead_added_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        fd_m_leads={"phone": "+13125550100"},
    )
    outbound_call = SimpleNamespace(
        call_id=uuid4(), src="3128781255", dst="+13125550100",
        call_date=date(2026, 9, 1), call_time=None,
    )
    # Same client number, but the client is calling IN (src), not an agent
    # calling out (dst) -- must not count as an outbound answer.
    inbound_call_same_number = SimpleNamespace(
        call_id=uuid4(), src="+13125550100", dst="3128781255",
        call_date=date(2026, 9, 1), call_time=None,
    )

    db = MagicMock()
    # _outbound_calls_by_lead now scopes this query to the leads' own phone
    # numbers in SQL (db.query(Call).filter(...).all()) instead of fetching
    # every call -- mock the .filter().all() chain, not .all() directly.
    db.query.return_value.options.return_value.filter.return_value.all.return_value = [outbound_call, inbound_call_same_number]

    with patch("routes.flintdesk.leads._configured_call_numbers", return_value={"3128781255"}):
        result = _outbound_calls_by_lead(db, [lead])

    assert result[lead.id] is outbound_call


def test_outbound_calls_by_lead_with_no_matching_call_is_empty():
    lead = SimpleNamespace(
        id=uuid4(),
        lead_added_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        fd_m_leads={"phone": "+13125550199"},
    )
    db = MagicMock()
    db.query.return_value.all.return_value = []

    with patch("routes.flintdesk.leads._configured_call_numbers", return_value=set()):
        result = _outbound_calls_by_lead(db, [lead])

    assert result == {}
