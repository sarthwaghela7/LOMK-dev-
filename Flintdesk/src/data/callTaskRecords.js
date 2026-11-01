export const TASK_LABELS = {
  missed_call: "Missed Call Task",
  non_responsiveness: "Non-Responsive Task",
};

// Badge shown under Call ID on the Calls page when a call matches a Lead
// (assigned on the Leads S1 page) by phone number -- see LEAD_TASK_LABEL's
// use in api.js's _buildCallRecordsWithTasks.
export const LEAD_TASK_LABEL = "Leads";

export function taskToCallsRecord(task = {}) {
  if (!Object.hasOwn(TASK_LABELS, task.task_type)) return null;
  const data = task.data || {};
  const missedCall = task.task_type === "missed_call";
  const label = TASK_LABELS[task.task_type];
  const recordDate = missedCall ? (data.missed_call_date || data.miscall_added_date) : data.date_added;
  const phone = missedCall ? data.contact_number : data.phone_number;
  const summary = data.summary || data.comments || "";
  const actionStatus = data.action_status || data.status || "Open";
  const caseType = missedCall ? "Missed Call" : (data.type || "Non-Responsive Call");
  const raw = {
    task_id: task.id,
    task_type: task.task_type,
    record_source: "task",
    fd_a_client_name: data.name || "",
    fd_a_client_type: caseType,
    fd_a_email_address: data.email || "",
    fd_a_call_summary: summary,
    fd_m_self_action_item: caseType,
    fd_m_manual_action_status: actionStatus,
    fd_m_blocker_comment: data.comments || "",
  };

  return {
    id: `TASK-${String(task.id || "").slice(0, 8)}`,
    callId: "",
    taskId: task.id,
    clientId: "",
    analysisId: null,
    agent: task.owner_name || task.assigned_by_name || "-",
    direction: label,
    date: recordDate || "-",
    registeredAt: task.submitted_at || "",
    timestamp: "-",
    duration: "-",
    billsec: null,
    extension: "",
    parentNumber: "",
    sourceNumber: phone || "",
    destinationNumber: "",
    capturedNumber: phone || "-",
    capturedNumbers: phone ? [phone] : [],
    clientNumber: phone || "-",
    caseType,
    status: "task-record",
    reviewStatusLabel: label,
    notes: summary,
    transcript: "",
    hasAudio: false,
    isTaskRecord: true,
    recordLabel: label,
    raw,
  };
}

export function callTaskRecords(tasks = []) {
  return tasks.map(taskToCallsRecord).filter(Boolean);
}
