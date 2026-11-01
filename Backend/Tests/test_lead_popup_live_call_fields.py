"""Lead ID popup shows what the agent entered on the linked call (not just the stale Analyse snapshot)."""
import os
from datetime import date, datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")

from routes.flintdesk import leads


class _Query:
    def __init__(self, result):
        self.result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self.result


class _Db:
    def __init__(self, lead, analysis):
        self.lead, self.analysis = lead, analysis

    def query(self, model):
        return _Query(self.lead if model is leads.FDLead else self.analysis)


def _analysis(**over):
    base = dict(
        call_type="Inbound", call_duration="00:05:00", fd_a_case_type="H1B", case_type=None,
        fd_a_case_worker_assigned="Sam", caseworker_assigned=None, fd_a_urgency_flag="High",
        escalation_to=None, fd_a_call_outcome="Consult booked", fd_a_call_summary="Edited summary",
        call_summary=None, enquiry_details="Q?", voicemail_left_if_no_answer=None,
        fd_m_self_action_item="Send retainer", fd_m_action_due_date=date(2026, 10, 20),
        fd_m_manual_action_status="Pending", fd_m_dependency="Docs", fd_m_follow_up_dates="10/21",
        fd_m_follow_up_attempts=2, fd_m_blocker_comment="Waiting on client", fd_m_case_status_change="Retained",
        fd_m_verification_comment="Verified", case_closing_completed=True,
        case_closing_completed_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_live_fields_carry_agent_entries_and_serialise_dates():
    out = leads._live_call_fields(_analysis())
    assert out["self_action_item"] == "Send retainer"
    assert out["action_due_date"] == "2026-10-20"
    assert out["action_status"] == "Pending"
    assert out["case_status"] == "Retained"
    assert out["follow_up_attempts"] == 2
    assert out["s3_completed"] is True and out["case_closing_date"].startswith("2026-10-22")
    assert out["call_summary"] == "Edited summary"


def test_unset_agent_fields_are_empty_not_none():
    out = leads._live_call_fields(_analysis(fd_m_self_action_item=None, fd_m_action_due_date=None))
    assert out["self_action_item"] == "" and out["action_due_date"] == ""


def test_popup_endpoint_overrides_stale_snapshot_with_live_call_values():
    lead = SimpleNamespace(
        fd_m_leads={"call_summary": "old snapshot", "matched_call_id": "c1", "name": "Ann"},
        fd_m_leads_lead_id="LD#43", fd_a_leads_call_id="c1", lead_added_at=None,
        fd_m_leads_owner_id=None, fd_m_leads_owner="x",
    )
    admin = {"role": "admin"}
    original = leads._can_manage_leads
    leads._can_manage_leads = lambda user, db=None: True
    try:
        body = leads.get_lead_by_public_id("LD#43", user=admin, db=_Db(lead, _analysis()))
    finally:
        leads._can_manage_leads = original
    assert body["lead_id"] == "LD#43" and body["name"] == "Ann"
    assert body["call_summary"] == "Edited summary"          # live, not "old snapshot"
    assert body["self_action_item"] == "Send retainer"
    assert lead.fd_m_leads["call_summary"] == "old snapshot"  # read-only: stored row untouched
