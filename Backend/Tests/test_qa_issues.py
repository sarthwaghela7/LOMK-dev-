import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from middleware.auth import get_current_user
from routes.calls import ManualFieldsUpdateRequest, assignment_business_date, update_manual_fields
from routes.flintdesk.dependencies import DependencyUpdate, _missing_agent_fields, dependency_dashboard_summary, update_dependency
from routes.flintdesk.leads import _lead_received_date
from routes.flintdesk.tasks import BulkDelete, delete_bulk
from services.flintdesk_notifications import add_notifications
from services.Processing_pipeline.fd_llm_node import FDLLM
from storage.models import CallAnalysisResult, FDTaskNotification


def _analysis_db(record):
    query = MagicMock()
    query.filter.return_value.first.return_value = record
    db = MagicMock()
    db.query.return_value = query
    return db


def _stage2_complete_analysis(**overrides):
    values = {
        "fd_m_self_action_item": "Call client back",
        "fd_m_action_due_date": date(2026, 9, 24),
        "fd_m_manual_action_status": "Open",
        "fd_m_dependency": "None",
        "fd_m_follow_up_dates": "09-25-2026",
        "fd_m_follow_up_attempts": 0,
        "fd_m_blocker_comment": "No blocker",
    }
    values.update(overrides)
    return CallAnalysisResult(**values)


class QAIssueRegressionTests(unittest.TestCase):
    def test_notification_payload_keeps_entity_and_change_context(self):
        recipient_id = uuid4()
        db = MagicMock()
        count = add_notifications(
            db, [recipient_id, recipient_id], title="Settings updated", message="Assignment changed",
            notification_type="settings_changed", source_page="settings", entity_id="assignment-7",
            change_context={"action": "assignment_updated", "extension": "101"},
        )
        self.assertEqual(count, 1)
        notification = db.add.call_args.args[0]
        self.assertEqual(notification.recipient_id, recipient_id)
        self.assertEqual(notification.entity_id, "assignment-7")
        self.assertEqual(notification.source_page, "settings")
        self.assertEqual(notification.change_context["action"], "assignment_updated")

    def test_dependency_approval_reports_all_missing_agent_fields(self):
        dependency = SimpleNamespace(
            call_id="CALL-1", client_enquiry_date=date(2025, 1, 13), question="",
            agent_enquiry_date=None, agent_research_about_query=None,
        )
        missing = _missing_agent_fields(dependency, DependencyUpdate(status="approved"))
        self.assertEqual(missing, ["Question", "Agent Enquiry Date", "Agent Research About the Query"])

    def test_submitted_task_delete_succeeds_with_notification_safe_fk(self):
        task = SimpleNamespace(id=uuid4(), task_type="general")
        query = MagicMock()
        query.filter.return_value.all.return_value = [task]
        db = MagicMock()
        db.query.return_value = query
        with patch("routes.flintdesk.tasks.can_edit", return_value=True):
            result = delete_bulk(BulkDelete(task_type="general", ids=[task.id]), {"role": "admin"}, db)
        self.assertEqual(result, {"deleted": 1})
        db.delete.assert_called_once_with(task)
        db.commit.assert_called_once()
        task_fk = next(iter(FDTaskNotification.__table__.c.task_id.foreign_keys))
        self.assertEqual(task_fk.ondelete, "SET NULL")

    def test_assigned_agent_can_mark_case_completed(self):
        db = _analysis_db(_stage2_complete_analysis(id=10, call_id="CALL-10"))
        with patch("routes.calls.assert_manual_call_access"), patch("routes.calls.assert_completion_access"):
            response = update_manual_fields(
                10, ManualFieldsUpdateRequest(case_closing_completed=True), db,
                {"role": "agent", "fd_user_id": str(uuid4())},
            )
        self.assertTrue(response["case_closing_completed"])
        db.commit.assert_called_once()

    def test_missing_bearer_credentials_returns_401(self):
        with self.assertRaises(HTTPException) as raised:
            get_current_user(None)
        self.assertEqual(raised.exception.status_code, 401)

    def test_permitted_completion_records_and_returns_timestamp(self):
        record = _stage2_complete_analysis(id=11, call_id="CALL-11")
        db = _analysis_db(record)
        with patch("routes.calls.assert_manual_call_access"):
            response = update_manual_fields(
                11, ManualFieldsUpdateRequest(case_closing_completed=True), db, {"role": "team_lead"},
            )
        self.assertTrue(record.case_closing_completed)
        self.assertIsNotNone(record.case_closing_completed_at)
        self.assertEqual(response["case_closing_completed_at"], record.case_closing_completed_at.isoformat())

    def test_completion_requires_every_stage2_field(self):
        db = _analysis_db(CallAnalysisResult(id=13, call_id="CALL-13"))
        with patch("routes.calls.assert_manual_call_access"), patch("routes.calls.assert_completion_access"), self.assertRaises(HTTPException) as raised:
            update_manual_fields(
                13, ManualFieldsUpdateRequest(case_closing_completed=True), db,
                {"role": "agent", "fd_user_id": str(uuid4())},
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("Stage 2", raised.exception.detail)

    def test_manual_update_changes_only_the_requested_manual_field(self):
        record = CallAnalysisResult(
            id=12,
            call_id=str(uuid4()),
            fd_a_call_summary="AI-owned summary",
            fd_m_self_action_item="Keep this",
        )
        call = SimpleNamespace(extension="101")
        query = MagicMock()
        query.filter.return_value.first.side_effect = [record, call]
        db = MagicMock()
        db.query.return_value = query

        with patch("routes.calls.assert_manual_call_access"):
            update_manual_fields(
                12,
                ManualFieldsUpdateRequest(fd_m_dependency="Client"),
                db,
                {"role": "agent", "fd_user_id": str(uuid4())},
            )

        self.assertEqual(record.fd_m_dependency, "Client")
        self.assertEqual(record.fd_m_self_action_item, "Keep this")
        self.assertEqual(record.fd_a_call_summary, "AI-owned summary")

    def test_dependency_dashboard_summary_returns_status_counts(self):
        row = SimpleNamespace(total=8, pending=3, approved=4, rejected=1)
        query = MagicMock()
        query.filter.return_value = query
        query.one.return_value = row
        db = MagicMock()
        db.query.return_value = query
        result = dependency_dashboard_summary(date(2025, 1, 1), date(2025, 1, 31), {"role": "admin"}, db)
        self.assertEqual(result, {"total": 8, "pending": 3, "approved": 4, "rejected": 1})
        self.assertEqual(query.filter.call_count, 2)

    def test_agent_can_view_all_dependencies_but_cannot_update_another_agents_record(self):
        owner_id = uuid4()
        other_agent_id = uuid4()
        dependency = SimpleNamespace(id=uuid4(), agent_id=owner_id)
        query = MagicMock()
        query.filter.return_value.first.return_value = dependency
        db = MagicMock()
        db.query.return_value = query
        with self.assertRaises(HTTPException) as raised:
            import asyncio
            asyncio.run(update_dependency(str(dependency.id), DependencyUpdate(question="Changed"), {"role": "agent", "fd_user_id": str(other_agent_id)}, db))
        self.assertEqual(raised.exception.status_code, 403)
        db.commit.assert_not_called()

    def test_lead_date_parser_is_explicit_us_format_and_normalizes_to_date(self):
        self.assertEqual(_lead_received_date("01/13/2025"), date(2025, 1, 13))
        self.assertEqual(_lead_received_date("2025-01-13"), date(2025, 1, 13))
        self.assertIsNone(_lead_received_date("13/01/2025"))

    def test_summary_guidance_scales_with_call_duration(self):
        self.assertIn("1-2 sentences", FDLLM.summary_length_guidance(SimpleNamespace(billsec=60)))
        self.assertIn("one concise paragraph", FDLLM.summary_length_guidance(SimpleNamespace(billsec=180)))
        self.assertIn("multi-paragraph", FDLLM.summary_length_guidance(SimpleNamespace(billsec=181)))
        self.assertIn("multi-paragraph", FDLLM.summary_length_guidance(SimpleNamespace(billsec=900)))

    def test_assignment_business_date_handles_central_midnight_boundary(self):
        boundary = datetime.fromisoformat("2026-08-31T04:30:00+00:00")
        self.assertEqual(assignment_business_date(boundary), date(2026, 8, 30))


if __name__ == "__main__":
    unittest.main()
