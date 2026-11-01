from sqlalchemy.orm import Session
from sqlalchemy import text
from uuid import uuid4
from datetime import datetime
from storage.db import engine
import json


def log_audit_event(
    user_id: str,
    action: str,
    status: str = "success",
    message: str = None,
    module_name: str = "auth",
    metadata: dict = None,
    organization_id: str = None
):
    """
    Log an audit event to system_audit_logs table.
    
    Args:
        user_id: UUID of the user performing the action
        action: Action performed (e.g., 'login', 'logout')
        status: Status of the action (e.g., 'success', 'failed')
        message: Optional message describing the event
        module_name: Module where action occurred (default: 'auth')
        metadata: Optional metadata dict for additional info
        organization_id: Optional organization UUID
    """
    try:
        audit_id = str(uuid4())
        created_at = datetime.utcnow()
        metadata_json = json.dumps(metadata) if metadata else "{}"
        
        with engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO system_audit_logs 
                    (id, user_id, action, status, message, module_name, metadata, organization_id, created_at)
                    VALUES (:id, :user_id, :action, :status, :message, :module_name, :metadata, :organization_id, :created_at)
                """),
                {
                    "id": audit_id,
                    "user_id": user_id,
                    "action": action,
                    "status": status,
                    "message": message,
                    "module_name": module_name,
                    "metadata": metadata_json,
                    "organization_id": organization_id,
                    "created_at": created_at
                }
            )
            conn.commit()
    except Exception as e:
        print(f"Error logging audit event: {str(e)}")
        # Don't raise - logging errors should not break auth flow
