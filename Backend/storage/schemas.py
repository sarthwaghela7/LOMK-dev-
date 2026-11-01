from pydantic import BaseModel
from uuid import UUID
from datetime import date, time, datetime
from typing import Optional

from datetime import datetime
from typing import Optional, Any

from pydantic import BaseModel, ConfigDict

class CallResponse(BaseModel):
    call_id: UUID
    src: Optional[str]
    dst: Optional[str]
    billsec: Optional[int]
    disposition: Optional[str]
    extension: Optional[str]
    extname: Optional[str]
    call_date: date
    call_time: time
    audio_s3_bucket: Optional[str]
    audio_s3_key: Optional[str]
    audio_s3_uri: Optional[str]
    transcript: Optional[str]
    processing_status: Optional[str]
    error_message: Optional[str]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True

    id: int
    call_transcript_id: Optional[int] = None
    call_id: str

    call_recording_date: Optional[str] = None
    call_type: Optional[str] = None
    call_duration: Optional[str] = None

    front_desk_person_name: Optional[str] = None
    client_name: Optional[str] = None
    client_phone_number: Optional[str] = None
    client_email: Optional[str] = None

    new_client_existing_client: Optional[str] = None
    source_if_new_client: Optional[str] = None

    case_type: Optional[str] = None
    caseworker_assigned: Optional[str] = None
    last_update_given_to_client: Optional[str] = None

    enquiry_details: Optional[str] = None
    call_summary: Optional[str] = None

    front_desk_action_required: Optional[str] = None
    client_action_required: Optional[str] = None

    follow_up_required: Optional[str] = None
    follow_up_deadline: Optional[str] = None
    follow_up_status: Optional[str] = None

    escalation_required: Optional[str] = None
    escalation_to: Optional[str] = None

    script_sop_followed: Optional[str] = None
    sop_steps_missed: Optional[str] = None

    conversation_preparedness: Optional[str] = None
    conversation_start_greeting: Optional[str] = None
    conversation_end_greeting: Optional[str] = None
    acknowledging_client_requirement_empathy: Optional[str] = None
    answer_quality: Optional[str] = None
    hold_protocol_followed: Optional[str] = None
    next_step_communicated_to_client: Optional[str] = None
    call_closed_properly: Optional[str] = None

    client_showed_signs_of_concern_or_anxiety: Optional[str] = None
    assurance_reassurance_provided_by_agent: Optional[str] = None
    paid_consultation_pitched: Optional[str] = None
    lead_source_asked: Optional[str] = None
    voicemail_left_if_no_answer: Optional[str] = None

    tone_conversation_check: Optional[str] = None
    client_sentiment: Optional[str] = None
    overall_call_quality_score: Optional[str] = None
    evaluator_notes: Optional[str] = None

    call_number: Optional[int] = None
    call_reference: Optional[str] = None

    raw_llm_json: Optional[Any] = None
    model_name: Optional[str] = None
    processing_status: Optional[str] = None
    fd_processing_status: Optional[str] = None

    created_at: Optional[datetime] = None

    fd_a_client_name: Optional[str] = None
    fd_a_client_type: Optional[str] = None
    fd_a_lead_source: Optional[str] = None
    fd_a_email_address: Optional[str] = None
    fd_a_urgency_flag: Optional[str] = None
    fd_a_case_type: Optional[str] = None
    fd_a_case_worker_assigned: Optional[str] = None
    fd_a_call_outcome: Optional[str] = None
    fd_a_call_summary: Optional[str] = None

    fd_m_self_action_item: Optional[str] = None
    fd_m_action_due_date: Optional[date] = None
    fd_m_manual_action_status: Optional[str] = None
    fd_m_dependency: Optional[str] = None
    fd_m_follow_up_dates: Optional[str] = None
    fd_m_follow_up_attempts: Optional[int] = None
    fd_m_blocker_comment: Optional[str] = None
    fd_m_case_status_change: Optional[str] = None
    fd_m_date_entry: Optional[date] = None
    fd_m_verification_comment: Optional[str] = None
    fd_m_attachment: Optional[str] = None
    fd_m_google_review: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)