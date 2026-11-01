from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Date,
    Time,
    Text,
    DateTime,
    BigInteger,
    Boolean,
    ForeignKey,
    UniqueConstraint,
    Computed,
    text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from storage.db import Base


class FDUser(Base):
    """FlintDesk login users — separate from the main `users` table."""
    __tablename__ = "fd_users"

    id = Column(UUID(as_uuid=True), primary_key=True)
    name = Column(Text, nullable=False)
    email = Column(Text, nullable=False, unique=True)
    pass_hash = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, server_default="true")
    role = Column(Text, nullable=False, server_default="agent")
    created_at = Column(DateTime(timezone=True))


class FDSMLead(Base):
    """Imported social-media lead (see migrations/061_create_sm_leads.py)."""
    __tablename__ = "fd_sm_leads"
    # RETURNING populates the sequence number and derived public ID on insert.
    __mapper_args__ = {"eager_defaults": True}

    id = Column(UUID(as_uuid=True), primary_key=True)
    sm_lead_number = Column(BigInteger, nullable=False, unique=True, server_default=text("nextval('sm_lead_number_seq')"))
    sm_lead_id = Column(Text, Computed("'SM#' || sm_lead_number::text", persisted=True), unique=True, index=True)
    date_added = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"), index=True)
    date_received = Column(Date)
    name = Column(Text)
    email = Column(Text)
    phone = Column(Text)
    phone_digits = Column(Text, index=True)
    enquiry_question = Column(Text)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("fd_users.id"), index=True)
    owner = Column(Text)
    status = Column(Text, nullable=False, server_default="Pending", index=True)
    submitted_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class FDCallUpdateEntry(Base):
    """Manual Call Updates value (see migrations/063_create_call_update_entries.py)."""
    __tablename__ = "fd_call_update_entries"
    __table_args__ = (UniqueConstraint("entry_date", "agent_id", "metric", name="fd_call_update_entries_entry_date_agent_id_metric_key"),)

    id = Column(UUID(as_uuid=True), primary_key=True)
    entry_date = Column(Date, nullable=False, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("fd_users.id", ondelete="CASCADE"), nullable=False)
    metric = Column(Text, nullable=False)
    value = Column(Integer, nullable=False)
    note = Column(Text)
    updated_by = Column(UUID(as_uuid=True), ForeignKey("fd_users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class FDSyncFilter(Base):
    """Numbers/extensions that F1Processor uses to decide which calls to sync.

    filter_type is either "number" or "extension"; only rows in this table
    are used to filter incoming calls (see services/Database_sync/f1.py).
    """
    __tablename__ = "fd_sync_filters"

    id = Column(UUID(as_uuid=True), primary_key=True)
    filter_type = Column(Text, nullable=False)
    value = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True))
    parent_number = Column(Text, nullable=True)
    target_page = Column(Text, nullable=True)


class FDAssignment(Base):
    """Extension assignment history with one current active row per extension."""
    __tablename__ = "fd_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True)
    assignment_date = Column(Date, nullable=False)
    extension = Column(Text, nullable=False)
    number = Column(Text, nullable=False)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("fd_users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="false")

    agent = relationship("FDUser")


class FDLead(Base):
    """Dedicated persistent source of truth for the FlintDesk Leads workspace."""
    __tablename__ = "fd_leads"

    id = Column(UUID(as_uuid=True), primary_key=True)
    # S1 manual/imported fields
    fd_m_leads_lead_id = Column(Text, nullable=False, unique=True, index=True)
    fd_m_leads_date_received = Column(Text)
    fd_m_leads_source = Column(Text)
    fd_m_leads_name = Column(Text)
    fd_m_leads_phone = Column(Text, index=True)
    fd_m_leads_email = Column(Text)
    fd_m_leads_client_type = Column(Text)
    fd_m_leads_client_profile = Column(Text)
    fd_m_leads_client_enquiry = Column(Text)
    fd_m_leads_lead_category = Column(Text)
    fd_m_leads_s1_status = Column(Text)
    # The name remains a display/audit value. Authorization must use the
    # canonical FlintDesk account id, never this mutable display name.
    fd_m_leads_owner = Column(Text)
    fd_m_leads_owner_id = Column(UUID(as_uuid=True), ForeignKey("fd_users.id"), index=True)
    fd_m_leads_extension = Column(Text, index=True)
    fd_m_leads_sid = Column(Text)
    # Needs selected on the intake form (see migrations/068_lead_sheet_sync.py).
    fd_m_leads_needs = Column(Text)
    # S2 AI-analysis copy/reference fields
    # Optional one-to-one call association. Leads are linked only by the
    # explicit Analyse action; calls and leads may both exist unlinked.
    fd_a_leads_call_id = Column(UUID(as_uuid=True), ForeignKey("calls.call_id", ondelete="SET NULL"), unique=True, index=True)
    fd_a_leads_agent_name = Column(Text); fd_a_leads_call_direction = Column(Text); fd_a_leads_call_duration = Column(Text)
    fd_a_leads_case_type = Column(Text); fd_a_leads_case_worker_assigned = Column(Text); fd_a_leads_urgency_flag = Column(Text)
    fd_a_leads_redirect_escalation_to = Column(Text); fd_a_leads_call_outcome = Column(Text); fd_a_leads_call_summary = Column(Text)
    fd_a_leads_enquiry_question = Column(Text); fd_a_leads_s2_status = Column(Text)
    # Stage 3 ("Leads Action Tracking") manual fields and the s3_completed
    # flag are intentionally NOT declared on this model anymore -- the Stage
    # 3 feature was removed from the Leads page. The underlying fd_leads
    # columns still physically exist in the database (dropping them is a
    # separate, deliberately not-yet-applied migration -- see
    # migrations/050_drop_leads_stage3_columns.py) so any historical Stage 3
    # data isn't lost by this application-level change alone.
    # Immutable server-generated timestamp for the import/add operation.
    # It deliberately has a business-specific name so it cannot be confused
    # with other entities' generic audit timestamps. Renamed from created_at
    # (see migrations/052_rename_lead_added_at.py) to match the production
    # database, which already has this column under its new name.
    match_status = Column(Text); match_count = Column(Integer); lead_added_at = Column(DateTime(timezone=True), nullable=False)

    _lead_field_columns = {
        "lead_id": "fd_m_leads_lead_id", "date_received": "fd_m_leads_date_received", "source": "fd_m_leads_source",
        "name": "fd_m_leads_name", "phone": "fd_m_leads_phone", "email": "fd_m_leads_email",
        "client_type": "fd_m_leads_client_type", "client_profile": "fd_m_leads_client_profile",
        "client_enquiry": "fd_m_leads_client_enquiry", "lead_category": "fd_m_leads_lead_category",
        "s1_status": "fd_m_leads_s1_status", "owner": "fd_m_leads_owner", "owner_id": "fd_m_leads_owner_id", "extension": "fd_m_leads_extension", "sid": "fd_m_leads_sid", "needs": "fd_m_leads_needs",
        "matched_call_id": "fd_a_leads_call_id", "agent_name": "fd_a_leads_agent_name", "call_direction": "fd_a_leads_call_direction",
        "call_duration": "fd_a_leads_call_duration", "case_type": "fd_a_leads_case_type",
        "case_worker_assigned": "fd_a_leads_case_worker_assigned", "urgency_flag": "fd_a_leads_urgency_flag",
        "redirect_escalation_to": "fd_a_leads_redirect_escalation_to", "call_outcome": "fd_a_leads_call_outcome",
        "call_summary": "fd_a_leads_call_summary", "enquiry_question": "fd_a_leads_enquiry_question", "s2_status": "fd_a_leads_s2_status",
    }

    @property
    def fd_m_leads(self):
        data = {key: getattr(self, column) for key, column in self._lead_field_columns.items() if getattr(self, column) not in (None, "")}
        if self.match_status: data["match_status"] = self.match_status
        if self.match_count is not None: data["match_count"] = self.match_count
        return data

    @fd_m_leads.setter
    def fd_m_leads(self, values):
        for key, value in (values or {}).items():
            column = self._lead_field_columns.get(key)
            if column: setattr(self, column, value)
            elif key == "match_status": self.match_status = value
            elif key == "match_count": self.match_count = value


class FDLeadSheetRow(Base):
    """Ledger of Google Sheet rows already seen (migrations/068_lead_sheet_sync.py)."""
    __tablename__ = "fd_lead_sheet_rows"

    id = Column(UUID(as_uuid=True), primary_key=True)
    row_key = Column(Text, nullable=False, unique=True)
    sheet_row_number = Column(Integer)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("fd_leads.id", ondelete="SET NULL"))
    status = Column(Text, nullable=False)
    error = Column(Text)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class FDTask(Base):
    __tablename__ = "tasks"
    id = Column(UUID(as_uuid=True), primary_key=True)
    task_type = Column(Text, nullable=False)
    fd_m_tasks_date = Column(Date); fd_m_tasks_tasks = Column(Text); fd_m_tasks_timeline = Column(Text)
    fd_m_tasks_owner = Column(UUID(as_uuid=True), ForeignKey("fd_users.id")); fd_m_tasks_status = Column(Text); fd_m_tasks_comments = Column(Text)
    fd_m_tasks_missed_call_date = Column(Date); fd_m_tasks_miscall_added_date = Column(Date); fd_m_tasks_name = Column(Text)
    fd_m_tasks_contact_number = Column(Text); fd_m_tasks_summary = Column(Text); fd_m_tasks_action_status = Column(Text)
    fd_m_tasks_date_added = Column(Date); fd_m_tasks_type = Column(Text); fd_m_tasks_phone_number = Column(Text); fd_m_tasks_email = Column(Text)
    # Exact source-call link, set only for Missed Call tasks auto-created
    # from a real Call row (dst = 602 or 2243860601 -- see
    # services/missed_call_sync.py). NULL for every manually imported row,
    # which keeps matching to a Call via the existing phone+date heuristic
    # in routes/flintdesk/tasks.py.
    fd_m_tasks_call_id = Column(UUID(as_uuid=True), ForeignKey("calls.call_id", ondelete="SET NULL"), nullable=True)
    # "front_desk_agent" or "website" -- set only for Missed Call tasks
    # auto-created from a real Call row, computed once at sync time (see
    # services/missed_call_sync.py and services/hannah_ai_routing.py). NULL
    # for manually imported rows and for any call session without a
    # qualifying route pair.
    fd_m_tasks_hannah_ai_category = Column(Text, nullable=True)
    # General-task audit field.  `owner` needs an explicit foreign key now
    # that tasks can also record the privileged user who assigned the task.
    assigned_by_id = Column(UUID(as_uuid=True), ForeignKey("fd_users.id"))
    created_at = Column(DateTime(timezone=True)); updated_at = Column(DateTime(timezone=True)); submitted_at = Column(DateTime(timezone=True))
    owner = relationship("FDUser", foreign_keys=[fd_m_tasks_owner])


class FDTodo(Base):
    __tablename__ = "todos"

    id = Column(UUID(as_uuid=True), primary_key=True)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("fd_users.id", ondelete="CASCADE"), index=True)
    title = Column(Text, nullable=False)
    description = Column(Text)
    status = Column(Text, nullable=False, server_default="To Do")
    entry_date = Column(Date, nullable=False)
    due_date = Column(Date)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class FDVisaUpdate(Base):
    __tablename__ = "visa_updates"

    id = Column(UUID(as_uuid=True), primary_key=True)
    case_type = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    details = Column(Text, nullable=False)
    reference_link = Column(Text)
    update_date = Column(Date, nullable=False)
    status = Column(Text, nullable=False, server_default="published")
    created_by = Column(UUID(as_uuid=True), ForeignKey("fd_users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class FDVisaUpdateQuestion(Base):
    __tablename__ = "visa_update_questions"

    id = Column(UUID(as_uuid=True), primary_key=True)
    update_id = Column(UUID(as_uuid=True), ForeignKey("visa_updates.id", ondelete="CASCADE"), nullable=False)
    question = Column(Text, nullable=False)
    question_type = Column(Text, nullable=False)
    options = Column(JSONB)
    correct_answer = Column(JSONB)
    required = Column(Boolean, nullable=False, server_default="true")
    position = Column(Integer, nullable=False, server_default="0")


class FDVisaUpdateAcknowledgement(Base):
    __tablename__ = "visa_update_acknowledgements"
    __table_args__ = (UniqueConstraint("user_id", "update_id", name="uq_visa_update_ack_user_update"),)

    id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("fd_users.id", ondelete="CASCADE"), nullable=False)
    update_id = Column(UUID(as_uuid=True), ForeignKey("visa_updates.id", ondelete="CASCADE"), nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=False)


class FDVisaUpdateResponse(Base):
    __tablename__ = "visa_update_responses"
    __table_args__ = (UniqueConstraint("user_id", "question_id", name="uq_visa_response_user_question"),)

    id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("fd_users.id", ondelete="CASCADE"), nullable=False)
    update_id = Column(UUID(as_uuid=True), ForeignKey("visa_updates.id", ondelete="CASCADE"), nullable=False)
    question_id = Column(UUID(as_uuid=True), ForeignKey("visa_update_questions.id", ondelete="CASCADE"), nullable=False)
    answer = Column(JSONB, nullable=False)
    submitted_at = Column(DateTime(timezone=True), nullable=False)


class FDTaskNotification(Base):
    __tablename__ = "task_notifications"
    id = Column(UUID(as_uuid=True), primary_key=True); task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    # This existing recipient notification stream is shared by tasks and
    # leads; exactly one source id is set by the creating service.
    lead_id = Column(UUID(as_uuid=True), ForeignKey("fd_leads.id", ondelete="SET NULL"), nullable=True)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("fd_users.id", ondelete="CASCADE"), nullable=False); message = Column(Text, nullable=False)
    title = Column(Text, nullable=False, server_default="Notification")
    notification_type = Column(Text, nullable=False, server_default="general_task_assignment")
    source_page = Column(Text, nullable=True)
    entity_id = Column(Text, nullable=True)
    change_context = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False); read_at = Column(DateTime(timezone=True))
    # Persistent case-closure notifications (see services/case_closure.py).
    dismissed_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    closure_id = Column(UUID(as_uuid=True), ForeignKey("fd_case_closures.id", ondelete="CASCADE"), nullable=True)


# ==================================================
# CHANGELOG (Business page support)
# ==================================================
# Added: CallAnalysisResult.business_opportunity (Text) — see the field
# itself below for details. No other models/columns were touched.
# ==================================================


class Call(Base):
    __tablename__ = "calls"

    call_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    # Permanent person identity. call_id continues to identify this one call.
    client_id = Column(Text, ForeignKey("clients.client_id"), nullable=True, index=True)
    src = Column(String, nullable=True)
    dst = Column(String, nullable=True)
    billsec = Column(Integer, nullable=True)
    disposition = Column(String, nullable=True)
    extension = Column(String, nullable=True)
    extname = Column(String, nullable=True)
    call_date = Column(Date, nullable=False)
    call_time = Column(Time, nullable=False)
    audio_s3_bucket = Column(Text, nullable=True)
    audio_s3_key = Column(Text, nullable=True)
    audio_s3_uri = Column(Text, nullable=True)
    # Authoritative recording filename from Vitel's CDR (``callrecordfilename``).
    # Preferred over the legacy timestamp/duration audio matcher when present.
    vitel_recording_filename = Column(Text, nullable=True)
    transcript = Column(Text, nullable=True)
    processing_status = Column(Text, nullable=True)
    fd_processing_status = Column(Text, nullable=True, server_default="not processed")
    # Row-claiming for concurrent F3 workers/processes (SELECT ... FOR UPDATE SKIP LOCKED).
    fd_claimed_at = Column(DateTime(timezone=True), nullable=True)
    fd_claimed_by = Column(Text, nullable=True)
    stt_claimed_at = Column(DateTime(timezone=True), nullable=True)
    stt_claimed_by = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    registered_at = Column(DateTime(timezone=True), nullable=True)
    # User friendly call number
    call_number = Column(Integer, nullable=True, index=True)
    call_reference = Column(Text, unique=True, nullable=True)

    client = relationship("Client", back_populates="calls")

    # This legacy database constraint protects concurrent insertion of the
    # exact same Vitel CDR. F1 additionally uses the stable
    # date/time/src/dst identity so a later billsec revision cannot create a
    # second operational call row.
    __table_args__ = (
        UniqueConstraint(
            "call_date", "call_time", "src", "dst", "billsec",
            name="uq_calls_date_time_src_dst_billsec",
        ),
    )


class Client(Base):
    """A permanent client identity, shared by all calls from one phone number."""
    __tablename__ = "clients"

    client_id = Column(Text, primary_key=True, nullable=False)
    # This is always the normalized matching value, never the display format.
    phone_number = Column(Text, nullable=False, unique=True, index=True)
    # Editable identity fields; the source of truth for this client_id once set,
    # overriding whatever name a given call's AI analysis happened to extract.
    client_name = Column(Text)
    client_email = Column(Text)
    first_call_date = Column(Date, nullable=False)
    last_call_date = Column(Date, nullable=False)
    total_calls = Column(Integer, nullable=False, server_default="0")
    client_status = Column(Text, nullable=False, server_default="Open")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    calls = relationship("Call", back_populates="client")

class CallAnalysisResult(Base):
    __tablename__ = "call_analysis_results"

    id = Column(BigInteger, primary_key=True)
    call_transcript_id = Column(BigInteger)
    # UNIQUE: a call can have AT MOST ONE call_analysis_results row. This is
    # enforced at the DB level (see migrations/002_dedupe_call_analysis.py)
    # so a call re-queued for processing (e.g. after a late/corrected audio
    # match) always updates its existing row instead of ever inserting a
    # second one for the same call_id.
    call_id = Column(Text, nullable=False, unique=True)
    call_number = Column(Integer, nullable=True)
    call_reference = Column(Text, nullable=True)
    call_recording_date = Column(Text)
    # Parsed/normalized copy of call_recording_date (real Date column,
    # indexed) so date-range queries can filter/sort in SQL instead of
    # pulling every row into Python. Backfilled by migrations/004_*.
    call_recording_date_normalized = Column(Date)
    call_type = Column(Text)
    call_duration = Column(Text)
    front_desk_person_name = Column(Text)
    # Typed by an agent in FlintDesk; never written by the AutoFlint model.
    # Manual agent-name shadow column disabled (column still exists; migration 064).
    # front_desk_person_name_manual = Column(Text)
    client_name = Column(Text)
    client_phone_number = Column(Text)
    client_email = Column(Text)
    new_client_existing_client = Column(Text)
    source_if_new_client = Column(Text)
    case_type = Column(Text)
    caseworker_assigned = Column(Text)
    last_update_given_to_client = Column(Text)
    enquiry_details = Column(Text)
    call_summary = Column(Text)
    front_desk_action_required = Column(Text)
    client_action_required = Column(Text)
    follow_up_required = Column(Text)
    follow_up_deadline = Column(Text)
    follow_up_status = Column(Text)
    escalation_required = Column(Text)
    escalation_to = Column(Text)
    # ==================================================
    # Business page field  <-- NEW (added for Business page support)
    # ==================================================
    # LLM-generated. Possible values: "YES" | "NO" | "N/A".
    # Column already exists on call_analysis_results (no migration
    # required), mirroring how review_status/action_status were added.
    business_opportunity = Column(Text)
    script_sop_followed = Column(Text)
    sop_steps_missed = Column(Text)
    conversation_preparedness = Column(Text)
    conversation_start_greeting = Column(Text)
    conversation_end_greeting = Column(Text)
    acknowledging_client_requirement_empathy = Column(Text)
    answer_quality = Column(Text)
    hold_protocol_followed = Column(Text)
    next_step_communicated_to_client = Column(Text)
    call_closed_properly = Column(Text)
    client_showed_signs_of_concern_or_anxiety = Column(Text)
    assurance_reassurance_provided_by_agent = Column(Text)
    paid_consultation_pitched = Column(Text)
    lead_source_asked = Column(Text)
    voicemail_left_if_no_answer = Column(Text)
    tone_conversation_check = Column(Text)
    client_sentiment = Column(Text)
    overall_call_quality_score = Column(Text)
    evaluator_notes = Column(Text)
    # ==================================================
    # Client FAQ field  <-- NEW (added for Client FAQ page support)
    # ==================================================
    # LLM-generated. The literal question(s) the client asked during the
    # call, if any (NULL/empty when the client didn't ask anything).
    client_question = Column(Text)
    # ==================================================
    # Pending Cases fields
    # ==================================================
    action_status = Column(Text)
    action_date = Column(Date)
    remarks = Column(Text)
    # ==================================================
    # Success Cases fields
    # ==================================================
    # These columns already exist on call_analysis_results (per task spec:
    # "no migration required unless absolutely necessary"). Mapping them
    # here so the Success Cases endpoints can read/write them the same way
    # Pending Cases reads/writes action_status/action_date/remarks.
    review_status = Column(Text)  # "Review Received" | "Review Not Received" (NULL treated as "Review Not Received" by the API layer, DB value untouched)
    review_status_comment = Column(Text)
    review_status_updated_date = Column(Date)
    fd_m_stage4_attachment = Column(Text)
    raw_llm_json = Column(JSONB)
    model_name = Column(Text)
    processing_status = Column(Text)
    created_at = Column(DateTime)
    # ==================================================
    # FlintDesk AI-generated analysis fields.
    # These are intentionally separate from the AutoFlint LLM result set and
    # are only populated by the FlintDesk Analyse trigger.
    # ==================================================
    fd_a_client_name = Column(Text)
    fd_a_client_type = Column(Text)
    fd_a_lead_source = Column(Text)
    fd_a_email_address = Column(Text)
    fd_a_urgency_flag = Column(Text)
    fd_a_case_type = Column(Text)
    fd_a_case_worker_assigned = Column(Text)
    fd_a_call_outcome = Column(Text)
    fd_a_call_summary = Column(Text)
    # Human correction displayed in preference to the AI-extracted name.
    # Keeping this separate prevents a manual correction from overwriting AI.
    fd_m_client_name = Column(Text)

    # ==================================================
    # FlintDesk manual fields (agent-entered, not LLM-generated)
    # ==================================================
    fd_m_self_action_item = Column(Text)
    fd_m_action_due_date = Column(Date)
    fd_m_action_due_note = Column(Text)
    fd_m_manual_action_status = Column(Text)
    fd_m_dependency = Column(Text)
    fd_m_follow_up_dates = Column(Text)
    fd_m_follow_up_attempts = Column(Integer)
    fd_m_blocker_comment = Column(Text)
    fd_m_case_status_change = Column(Text)
    # Case lifecycle (migration 073). Maintained by the fd_case_lifecycle trigger and the Lost processor; never set by routes.
    case_lifecycle_stage = Column(Text)
    in_transition_entered_at = Column(DateTime(timezone=True))
    last_in_transition_status = Column(Text)
    lost_at = Column(DateTime(timezone=True))
    case_lifecycle_updated_at = Column(DateTime(timezone=True))
    fd_m_date_entry = Column(Date)
    fd_m_verification_comment = Column(Text)
    fd_m_attachment = Column(Text)
    fd_m_google_review = Column(Text)
    manual_data_score = Column(Float, nullable=True)
    manual_data_score_reason = Column(Text, nullable=True)
    manual_data_scored_at = Column(DateTime(timezone=True), nullable=True)
    # ==================================================
    # Case Closing fields (Case Closing page support)
    # ==================================================
    # Paid Consultation Lead Score (0-100)
    # Scoring: 70-100 (BOOK NOW), 40-69 (GATHER MORE INFO), 0-39 (NURTURE)
    lead_score = Column(Integer, nullable=True)
    
    # Business Opportunity Score (0-10)
    # Scoring: 8-10 (HIGH), 7-7.9 (HIGH Mid), 4-6.9 (STANDARD), 0/N/A (N/A)
    bos_score = Column(Float, nullable=True)
    
    # Computed priority tier based on lead_score, bos_score, and urgency_override
    # Values: BOOK_NOW, GATHER_MORE_INFO, NURTURE, HIGH, HIGH_MID, STANDARD, N_A
    priority_tier = Column(Text, nullable=True)
    # Which Stage 2 values the AI wrote, per field: value, prompt key/version, time.
    # A value is "manual" once it differs from the recorded AI value.
    fd_stage2_ai_meta = Column(JSONB)
    
    # Urgency override flags (NOID, RFE, COURT_DATE, CLIENT_DISTRESSED, NONE)
    # Overrides normal scoring to qualify for same-day consultation
    urgency_override = Column(Text, nullable=True, server_default="NONE")
    
    # Case Closing completion status - set when user marks case as completed
    case_closing_completed = Column(Boolean, nullable=True, server_default="false")
    case_closing_completed_at = Column(DateTime(timezone=True), nullable=True)

class SyncLog(Base):
    __tablename__ = "sync_logs"

    sync_log_id = Column(UUID(as_uuid=True), primary_key=True)
    source_system = Column(String, nullable=False)
    sync_action = Column(String, nullable=False)
    batch_date = Column(Date)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    status = Column(String, nullable=False)
    total_records = Column(Integer)
    synced_records = Column(Integer)
    failed_records = Column(Integer)
    skipped_records = Column(Integer)
    last_synced_call_date = Column(DateTime(timezone=True))
    file_path = Column(Text)
    error_message = Column(Text)
    details = Column(JSONB)
    created_by = Column(UUID(as_uuid=True))
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))

class SystemAuditLog(Base):
    __tablename__ = "system_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default="uuid_generate_v4()")
    organization_id = Column(UUID(as_uuid=True), nullable=True)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    module_name = Column(String, nullable=True)
    action = Column(String, nullable=True)
    status = Column(String, nullable=True)
    message = Column(Text, nullable=True)
    meta_data = Column(JSONB, nullable=True, server_default="'{}'::jsonb", name="metadata")
    created_at = Column(DateTime(timezone=True), nullable=True, server_default="now()")


class Dependency(Base):
    """FlintDesk Dependencies tracking — questions and follow-ups on calls."""
    __tablename__ = "fd_dependencies"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default="uuid_generate_v4()")
    # User-entered call reference
    call_id = Column(Text, nullable=True, index=True)
    # Date the client made the initial enquiry
    client_enquiry_date = Column(Date, nullable=True, index=True)
    # Summary of the call (fetched from call_analysis_results, not manually entered)
    call_summary = Column(Text, nullable=True)
    # The question/dependency from the call
    question = Column(Text, nullable=True)
    # Date the agent followed up on the dependency
    agent_enquiry_date = Column(Date, nullable=True)
    # Reuse the existing fd_users account instead of a duplicate agent map.
    agent_id = Column(UUID(as_uuid=True), ForeignKey("fd_users.id"), nullable=True)
    agent_research_about_query = Column(Text, nullable=True)
    # Admin-only: who is responsible for answering
    answer_by = Column(Text, nullable=True)
    # Admin-only: link to the answer/documentation
    answer_doc_link = Column(Text, nullable=True)
    # Admin-only: date the answer was provided
    answer_date = Column(Date, nullable=True)
    subject_matter_expert = Column(Text, nullable=True)
    subject_matter_expert_comment = Column(Text, nullable=True)
    status = Column(Text, nullable=False, server_default="pending")
    # Metadata
    created_at = Column(DateTime(timezone=True), nullable=False, server_default="now()")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default="now()")
    agent = relationship("FDUser")


class FDPrompt(Base):
    """Versioned FlintDesk AI prompt; one active row per prompt_key."""
    __tablename__ = "fd_prompts"

    id = Column(BigInteger, primary_key=True)
    prompt_key = Column(Text, nullable=False)
    prompt_name = Column(Text, nullable=False)
    system_prompt = Column(Text, nullable=False)
    user_prompt_template = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, server_default="1")
    is_active = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_by = Column(Text)


class FDCaseClosure(Base):
    """One closing event: the authoritative closer, status and case context."""
    __tablename__ = "fd_case_closures"

    id = Column(UUID(as_uuid=True), primary_key=True)
    client_id = Column(Text, nullable=False, index=True)
    closing_analysis_id = Column(BigInteger, nullable=False)
    closing_call_id = Column(UUID(as_uuid=True))
    closing_user_id = Column(UUID(as_uuid=True))
    closing_agent_name = Column(Text)
    closure_status = Column(Text, nullable=False)
    context_key = Column(Text, nullable=False)
    case_context = Column(Text)
    closed_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    reverted_at = Column(DateTime(timezone=True))
    reverted_by = Column(UUID(as_uuid=True))


class FDCaseClosureLink(Base):
    """Another agent's log that confirmed a closure. The log's own status is never rewritten."""
    __tablename__ = "fd_case_closure_links"

    id = Column(UUID(as_uuid=True), primary_key=True)
    closure_id = Column(UUID(as_uuid=True), ForeignKey("fd_case_closures.id", ondelete="CASCADE"), nullable=False)
    analysis_id = Column(BigInteger, nullable=False)
    call_id = Column(UUID(as_uuid=True))
    agent_user_id = Column(UUID(as_uuid=True))
    confirmed_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    confirmed_by = Column(UUID(as_uuid=True))
    reverted_at = Column(DateTime(timezone=True))
