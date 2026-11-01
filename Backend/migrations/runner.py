"""Explicit release-time migrations with an atomic ledger and bounded locks.

Run: python -m migrations.runner
Existing installations must baseline only after verifying their schema/data;
--baseline records the legacy manifest without replaying its data migrations.
"""
import argparse
from contextlib import contextmanager
from importlib import import_module

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

MIGRATIONS = (
    ("migrations.008_create_fd_users", "migrate_create_fd_users"),
    ("migrations.009_add_flintdesk_fields", "migrate_add_flintdesk_fields"),
    ("migrations.010_fix_fd_processing_status", "migrate_fix_fd_processing_status"),
    ("migrations.011_create_fd_sync_filters", "migrate_create_fd_sync_filters"),
    ("migrations.012_create_fd_assignments", "migrate_create_fd_assignments"),
    ("migrations.013_add_601_sync_filters", "migrate_add_601_sync_filters"),
    ("migrations.014_add_stage4_attachment", "migrate_add_stage4_attachment"),
    ("migrations.016_fix_call_recording_date_normalized", "migrate_fix_call_recording_date_normalized"),
    ("migrations.017_performance_tracking", "migrate_performance_tracking"),
    ("migrations.017_add_case_closing_fields", "migrate_add_case_closing_fields"),
    ("migrations.018_seed_fd_admin_and_fix_roles", "migrate_fd_roles"),
    ("migrations.019_create_dependencies_table", "migrate_create_dependencies"),
    ("migrations.020_client_lifecycle", "migrate_client_lifecycle"),
    ("migrations.021_fd_roles_and_dependency_review", "migrate_fd_roles_and_dependency_review"),
    ("migrations.022_add_fd_m_leads", "migrate_add_fd_m_leads"),
    ("migrations.024_create_tasks", "migrate_create_tasks"),
    ("migrations.025_fix_fd_assignment_config", "migrate_fix_fd_assignment_config"),
    ("migrations.026_update_fd_page_routes", "migrate_update_fd_page_routes"),
    ("migrations.027_create_fd_leads", "migrate_create_fd_leads"),
    ("migrations.028_remove_legacy_call_analysis_leads", "migrate_remove_legacy_call_analysis_leads"),
    ("migrations.029_add_special_lead_extensions", "migrate_add_special_lead_extensions"),
    ("migrations.030_general_task_notifications", "migrate_general_task_notifications"),
    ("migrations.031_lead_owner_assignments", "migrate_lead_owner_assignments"),
    ("migrations.032_lead_notification_delete_behavior", "migrate_lead_notification_delete_behavior"),
    ("migrations.033_qa_notifications_and_task_cleanup", "migrate_qa_notifications_and_task_cleanup"),
    ("migrations.034_case_completion_timestamp", "migrate_case_completion_timestamp"),
    ("migrations.035_lead_completion_fields", "migrate_lead_completion_fields"),
    ("migrations.036_lead_call_relationship", "migrate_lead_call_relationship"),
    ("migrations.037_create_todos", "migrate_create_todos"),
    ("migrations.039_create_visa_updates", "migrate_create_visa_updates"),
    ("migrations.040_manual_data_score", "migrate_manual_data_score"),
    ("migrations.041_persistent_fd_assignments", "migrate_persistent_fd_assignments"),
    ("migrations.042_add_manual_client_name", "migrate_add_manual_client_name"),
    ("migrations.043_add_client_profile_fields", "migrate_add_client_profile_fields"),
    ("migrations.044_add_performance_indexes", "migrate_add_performance_indexes"),
    ("migrations.045_add_fd_claim_columns", "migrate_fd_claim_columns"),
    ("migrations.046_analysis_jobs", "migrate_analysis_jobs"),
    ("migrations.047_dedupe_calls_and_unique_constraint", "migrate_dedupe_calls_and_unique_constraint"),
    ("migrations.048_add_missed_call_sync_filters", "migrate_add_missed_call_sync_filters"),
    ("migrations.049_add_fd_m_tasks_call_id", "migrate_add_fd_m_tasks_call_id"),
    ("migrations.051_add_action_due_note", "migrate_action_due_note"),
    ("migrations.052_rename_lead_added_at", "migrate_rename_lead_added_at"),
    ("migrations.053_add_website_hannah_ai_sync_filter", "migrate_add_website_hannah_ai_sync_filter"),
    ("migrations.054_add_hannah_ai_category_to_tasks", "migrate_add_hannah_ai_category_to_tasks"),
    ("migrations.055_backfill_hannah_ai_category", "migrate_backfill_hannah_ai_category"),
    ("migrations.058_move_website_extensions_to_2243939098", "migrate_move_website_extensions_to_2243939098"),
    ("migrations.059_add_todo_owner", "migrate_add_todo_owner"),
    ("migrations.060_add_605_sync_filters", "migrate_add_605_sync_filters"),
    ("migrations.061_create_sm_leads", "migrate_create_sm_leads"),
    ("migrations.062_sm_leads_derived_completion", "migrate_sm_leads_derived_completion"),
    ("migrations.063_create_call_update_entries", "migrate_create_call_update_entries"),
    ("migrations.064_manual_agent_name", "migrate_manual_agent_name"),
    ("migrations.065_create_call_count_snapshots", "migrate_create_call_count_snapshots"),
    ("migrations.066_add_vitel_recording_filename", "migrate_add_vitel_recording_filename"),
    ("migrations.068_lead_sheet_sync", "migrate_lead_sheet_sync"),
    ("migrations.069_create_fd_prompts", "migrate_create_fd_prompts"),
    ("migrations.070_add_stage2_ai_meta", "migrate_add_stage2_ai_meta"),
    ("migrations.071_create_case_closures", "migrate_create_case_closures"),
    ("migrations.072_notification_closure_columns", "migrate_notification_closure_columns"),
    ("migrations.073_case_lifecycle", "migrate_case_lifecycle"),
    ("migrations.074_priority_cases", "migrate_priority_cases"),
    ("migrations.071_usage_monitoring", "migrate_usage_monitoring"),
    ("migrations.075_priority_prompt_contextual", "migrate_priority_prompt_contextual"),
    ("migrations.076_wrong_number_dialed", "migrate_wrong_number_dialed"),
    ("migrations.077_follow_up_retainer", "migrate_follow_up_retainer"),
)
LOCK_KEY = 739412806


class MigrationConnection:
    """Adapt legacy engine.begin() migrations to the ledger transaction."""
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection


def run_migrations(engine=None, *, baseline=False, manifest=MIGRATIONS):
    if engine is None:
        from storage.db import engine
    # Transaction advisory locks work through the transaction pooler. Hold the
    # lock, migration changes and ledger writes in the SAME transaction.
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL lock_timeout = '2s'"))
        conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        # transaction_timeout exists from PostgreSQL 17 (the version the release database runs); an older server rejects the
        # statement, so it is only issued where it is supported. The lock, statement and idle bounds apply on every version.
        if int(conn.execute(text("SHOW server_version_num")).scalar()) >= 170000:
            conn.execute(text("SET LOCAL transaction_timeout = '120s'"))
        conn.execute(text("SET LOCAL idle_in_transaction_session_timeout = '60s'"))
        if not conn.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_KEY}).scalar():
            raise RuntimeError("Another release migration is running")
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.app_schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                baselined BOOLEAN NOT NULL DEFAULT false
            )
        """))
        applied = set(conn.execute(text("SELECT version FROM public.app_schema_migrations")).scalars())
        for module_name, function_name in manifest:
            if module_name in applied:
                continue
            if not baseline:
                module = import_module(module_name)
                original_engine = getattr(module, "engine", None)
                original_sessions = getattr(module, "SessionLocal", None)
                try:
                    if original_engine is not None:
                        module.engine = MigrationConnection(conn)
                    if original_sessions is not None:
                        module.SessionLocal = sessionmaker(bind=conn, join_transaction_mode="rollback_only")
                    getattr(module, function_name)()
                finally:
                    if original_engine is not None:
                        module.engine = original_engine
                    if original_sessions is not None:
                        module.SessionLocal = original_sessions
            conn.execute(text("""
                INSERT INTO public.app_schema_migrations (version, baselined)
                VALUES (:version, :baselined)
            """), {"version": module_name, "baselined": baseline})
            print(f"{'Baselined' if baseline else 'Applied'} {module_name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="store_true",
                        help="Record the verified legacy manifest without replaying migrations")
    run_migrations(baseline=parser.parse_args().baseline)
