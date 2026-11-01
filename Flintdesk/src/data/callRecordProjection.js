import { callTaskRecords, TASK_LABELS, LEAD_TASK_LABEL } from "./callTaskRecords.js";
const STAGE_2_FIELDS = [
  "fd_m_self_action_item",
  "fd_m_action_due_date",
  "fd_m_manual_action_status",
  "fd_m_dependency",
  "fd_m_follow_up_dates",
  "fd_m_follow_up_attempts",
  "fd_m_blocker_comment",
];

const STAGE_3_FIELDS = [
  "fd_m_case_status_change",
  "fd_m_date_entry",
  "fd_m_verification_comment",
  "fd_m_attachment",
  "fd_m_stage4_attachment",
];

const STAGE_4_FIELDS = ["fd_m_google_review"];

function hasMeaningfulValue(value) {
  return value !== null && value !== undefined && String(value).trim() !== "";
}

export function formatAudioDuration(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return value || "-";
  const wholeSeconds = Math.floor(seconds);
  return `${Math.floor(wholeSeconds / 60)}:${String(wholeSeconds % 60).padStart(2, "0")}`;
}

// Stage 1 AI fields are intentionally excluded. Overall review status is based
// only on whether the required Stage 2, 3, and 4 fields are empty or filled.
export function calculateReviewStatus(record = {}) {
  const manualFields = [
    ...STAGE_2_FIELDS,
    ...STAGE_3_FIELDS,
    ...STAGE_4_FIELDS,
  ];
  const filledCount = manualFields.filter((field) =>
    hasMeaningfulValue(record[field]),
  ).length;

  if (filledCount === 0) return "needs-review";
  if (filledCount === manualFields.length) return "call-reviewed";
  return "partially-reviewed";
}

export function getReviewStatusLabel(status) {
  const labels = {
    "needs-review": "Needs Review",
    "partially-reviewed": "Pending Calls",
    "call-reviewed": "Completed Calls",
  };

  return labels[status] || labels["needs-review"];
}

// Shared by getCallsList (unpaginated) and getCallsListPaged (paginated):
// maps /calls/analysis rows into the shape used by the FlintDesk Calls
// table, and merges in task badges/synthetic rows. `calls` supplies
// audio/transcript/etc. for a row when the analysis row itself doesn't
// already carry them -- the paginated path's rows already do (see
// routes/calls.py serialize_analysis_rows), so it passes an empty array
// and every `call?.x` below simply falls through to the row's own field.
// `leads` supplies the Leads Task badge (see the linkedTasks merge below).
export function _buildCallRecordsWithTasks(rows, calls, tasks, leads, { includeUnmatchedTasks = true } = {}) {
  const callsById = new Map(calls.map((c) => [c.call_id, c]));
  // Keep calls that have not produced an analysis row yet. This lets the
  // existing All Calls filter include every call returned by the calls API.
  const analysisById = new Map(rows.map((row) => [row.call_id, row]));
  const records = [
    ...rows,
    ...calls.filter((call) => !analysisById.has(call.call_id)),
  ];

  const callRecords = records.map((row) => {
    const call = callsById.get(row.call_id);
    const assignedAgent =
      row.assigned_agent_name || call?.assigned_agent_name || "";
    // The backend already resolves ownership as: an agent who typed their
    // own name into the Agent cell (front_desk_person_name) overrides the
    // Lead Number + Extension assignment, since inbound calls on a shared
    // trunk line have no reliable per-extension owner until someone claims
    // them. Trust that already-merged value instead of re-deriving a
    // conflicting order here.
    const agent =
      row.front_desk_person_name || call?.front_desk_person_name || "-";
    // The Agent cell's edit endpoint (/calls/{id}/field) compares its
    // stale-write guard against the raw stored column, not the fallback
    // above -- so editing must start from this raw value, or self-
    // identifying on a call that's only showing the extension-assignment
    // fallback (nobody has actually typed a name in yet) always 409s,
    // since the raw column is still blank while `agent` already shows a
    // name. See routes/calls.py serialize_analysis_rows.
    const rawAgent = row.raw_front_desk_person_name || "";
    // A saved client profile is the lifecycle popup's source of truth. Keep
    // that value ahead of per-call analysis values in every list view.
    const clientName = String(call?.client_name || row.fd_m_client_name || row.fd_a_client_name || "").trim();
    const reviewStatus = calculateReviewStatus(row);
    const direction =
      row.call_direction || row.call_type || call?.call_type || "Not Provided";
    // A client's number is the remote party, never the assigned extension.
    // Keep the source database representation intact and leave it blank when
    // the direction/source pair cannot establish a client number.
    const clientPhoneNumber = /outbound/i.test(String(direction))
      ? call?.dst || row.dst || ""
      : /inbound/i.test(String(direction))
        ? call?.src || row.src || ""
        : "";
    const capturedNumbers = [
      ...new Set(
        [call?.src, call?.dst, row.src, row.dst]
          .map((value) => String(value || "").trim())
          .filter(Boolean),
      ),
    ];

    return {
      id: row.formatted_call_id || row.call_reference || row.call_id,
      callId: row.call_id,
      clientId: row.client_id || call?.client_id || "",
      leadId: row.lead_id || call?.lead_id || "",
      analysisId: row.id,
      agent,
      rawAgent,
      assignedAgent,
      direction,
      date:
        row.call_recording_date_normalized ||
        row.call_recording_date ||
        call?.call_date ||
        "-",
      registeredAt:
        call?.registered_at || row.registered_at || row.created_at || "",
      timestamp: row.call_time || call?.call_time || "-",
      duration: formatAudioDuration(row.call_duration || call?.billsec || row.billsec),
      billsec: call?.billsec ?? row.billsec ?? null,
      extension: call?.extension || row.extension || "-",
      parentNumber: call?.parent_number || row.parent_number || "",
      sourceNumber: call?.src || row.src || "",
      destinationNumber: call?.dst || row.dst || "",
      clientPhoneNumber,
      capturedNumber: capturedNumbers[0] || "-",
      capturedNumbers,
      clientNumber:
        row.client_phone_number ||
        row.phone_number ||
        row.client_number ||
        call?.src ||
        call?.dst ||
        "-",
      caseType: row.case_type || "Not Provided",
      status: reviewStatus,
      reviewStatusLabel: getReviewStatusLabel(reviewStatus),
      notes: row.evaluator_notes || "",
      transcript: call?.transcript || row.transcript || "",
      hasAudio: Boolean(
        call?.audio_s3_uri || call?.audio_s3_key || row.audio_s3_uri || row.audio_s3_key,
      ),
      selfActionItem: row.fd_m_self_action_item || "",
      actionDueDate: row.fd_m_action_due_date || "",
      manualActionStatus: row.fd_m_manual_action_status || "",
      dependency: row.fd_m_dependency || "",
      followUpDates: row.fd_m_follow_up_dates || "",
      followUpAttempts: row.fd_m_follow_up_attempts ?? "",
      blockerComment: row.fd_m_blocker_comment || "",
      raw: {
        ...row,
        // Client-profile edits are canonical and must survive the next list
        // fetch, rather than being replaced by stale per-call AI values.
        fd_a_client_name: call?.client_name || row.fd_a_client_name || "",
        fd_a_email_address: call?.client_email || row.fd_a_email_address || "",
        fd_m_client_name: call?.client_name || row.fd_m_client_name || "",
      },
    };
  });
  // A Non-Responsive task is only still "open work" when it hasn't been
  // matched to a real call yet (the backend already does that phone-number
  // match and returns it as data.call_id). Once it has, the real call and
  // the task describe the same event -- show the task as a small badge on
  // that call's row instead of a second, synthetic row.
  // Missed Call tasks originate from dst = 602/2243860601 calls (Hannah AI)
  // and belong only on the dedicated Missed Calls page. The backend already
  // excludes those calls from the calls/analysis endpoints above, but this
  // task merge is a separate source of rows -- drop missed_call tasks here
  // too, or they leak back onto the Calls page regardless of that filter.
  // (Its own Missed Call Task badge instead comes from the callback match
  // below, which points at the agent's real callback call, not Hannah's.)
  const nonMissedCallTasks = tasks.filter((task) => task.task_type !== "missed_call");
  const tasksByCallId = new Map(
    nonMissedCallTasks.filter((task) => task.data?.call_id).map((task) => [task.data.call_id, task]),
  );
  const unmatchedTasks = nonMissedCallTasks.filter((task) => !task.data?.call_id);
  // Missed Call Task badge: once an agent calls the client back and that
  // callback is synced in as a real Call, it should carry the badge -- not
  // Hannah AI's own original call. getTasks(..., { include_callback: true })
  // has the backend resolve this the same way the Missed Calls page's
  // "Called Back" column does (see _callback_match in routes/flintdesk/
  // tasks.py): the nearest outbound call to that phone number on/after the
  // miss.
  const missedCallByCallbackCallId = new Map(
    tasks
      .filter((task) => task.task_type === "missed_call" && task.data?.callback?.call_id)
      .map((task) => [task.data.callback.call_id, task]),
  );
  // Leads Task badge: must land on the exact same call that turns the
  // Leads S1 row green -- same_day_outbound_call_id (see
  // _same_day_outbound_calls_by_lead in routes/flintdesk/leads.py): same
  // client phone number, outbound, and made on the lead's Date of Lead
  // Added. Deliberately not call_details.call_id, which is a looser
  // nearest-date/either-direction match used only for that column's own
  // proposed Call ID display, not for "this is the call that closed the
  // lead."
  const leadByCallId = new Map(
    (leads || [])
      .filter((lead) => lead.same_day_outbound_call_id)
      .map((lead) => [lead.same_day_outbound_call_id, lead]),
  );
  const callRecordsWithTasks = callRecords.map((record) => {
    const directTask = tasksByCallId.get(record.callId);
    const callbackTask = missedCallByCallbackCallId.get(record.callId);
    const lead = leadByCallId.get(record.callId);
    const leadTask = lead && { id: lead.id, type: "lead", label: LEAD_TASK_LABEL };
    const linkedTasks = [
      directTask && { id: directTask.id, type: directTask.task_type, label: TASK_LABELS[directTask.task_type] },
      // A callback can resolve a Missed Call and also later be selected for a
      // Lead.  It remains evidence for the Missed Calls workflow, but the
      // Calls page must give the explicit Lead association priority so one
      // call row is never labelled as both task types.
      !leadTask && callbackTask && { id: callbackTask.id, type: callbackTask.task_type, label: TASK_LABELS[callbackTask.task_type] },
      leadTask,
    ].filter(Boolean);
    if (!linkedTasks.length) return record;
    return {
      ...record,
      // Kept alongside linkedTasks for any older caller that still reads
      // the single-badge shape -- always the same first entry.
      linkedTask: linkedTasks[0],
      linkedTasks,
    };
  });
  // Synthetic rows for a task with no call_id at all can't be attributed to
  // any one page of calls (they aren't a call), so the paginated fetch only
  // asks for these on page 1 -- see getCallsListPaged.
  return includeUnmatchedTasks
    ? [...callRecordsWithTasks, ...callTaskRecords(unmatchedTasks)]
    : callRecordsWithTasks;
}


export function buildDashboardCallRecords(rows, calls) {
  const callsById = new Map(calls.map((call) => [call.call_id, call]));
  const analysisById = new Map(rows.map((row) => [row.call_id, row]));
  return [
    ...rows,
    ...calls.filter((call) => !analysisById.has(call.call_id)),
  ].map((row) => {
    const call = callsById.get(row.call_id);
    const agent = row.front_desk_person_name || call?.front_desk_person_name || "-";
    // Match the canonical client profile used by the lifecycle popup.
    const clientName = String(call?.client_name || row.fd_m_client_name || row.fd_a_client_name || "").trim();
    const capturedNumbers = [
      ...new Set(
        [call?.src, call?.dst, row.src, row.dst]
          .map((value) => String(value || "").trim())
          .filter(Boolean),
      ),
    ];
    const reviewStatus = calculateReviewStatus(row);
    const direction =
      row.call_direction || row.call_type || call?.call_type || "Not Provided";
    const clientPhoneNumber = /outbound/i.test(String(direction))
      ? call?.dst || row.dst || ""
      : /inbound/i.test(String(direction))
        ? call?.src || row.src || ""
        : "";
    return {
      id: row.formatted_call_id || row.call_reference || row.call_id,
      callId: row.call_id,
      clientId: row.client_id || call?.client_id || "",
      leadId: row.lead_id || call?.lead_id || "",
      analysisId: row.id,
      agent,
      direction,
      date:
        row.call_recording_date_normalized ||
        row.call_recording_date ||
        call?.call_date ||
        "-",
      registeredAt:
        call?.registered_at || row.registered_at || row.created_at || "",
      timestamp: row.call_time || call?.call_time || "-",
      duration: formatAudioDuration(row.call_duration || call?.billsec),
      billsec: call?.billsec ?? row.billsec ?? null,
      extension: call?.extension || row.extension || "-",
      parentNumber: call?.parent_number || row.parent_number || "",
      sourceNumber: call?.src || row.src || "",
      destinationNumber: call?.dst || row.dst || "",
      clientPhoneNumber,
      capturedNumber: capturedNumbers[0] || "-",
      capturedNumbers,
      clientNumber:
        row.client_phone_number ||
        row.phone_number ||
        row.client_number ||
        call?.src ||
        call?.dst ||
        "-",
      caseType: row.case_type || "Not Provided",
      status: reviewStatus,
      reviewStatusLabel: getReviewStatusLabel(reviewStatus),
      raw: {
        ...row,
        fd_a_client_name: call?.client_name || row.fd_a_client_name || "",
        fd_a_email_address: call?.client_email || row.fd_a_email_address || "",
        fd_m_client_name: call?.client_name || row.fd_m_client_name || "",
      },
    };
  });
}
