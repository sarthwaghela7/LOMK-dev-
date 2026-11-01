import { callTaskRecords, TASK_LABELS, LEAD_TASK_LABEL } from "./callTaskRecords.js";

// Local development talks to the local FastAPI process. Production must never
// fall back to the browser's localhost: the deployed browser has no API there.
const defaultApiBaseUrl =
  typeof window !== "undefined" &&
  ["localhost", "127.0.0.1"].includes(window.location.hostname)
    ? "http://localhost:8000"
    : "https://autoflintbackend-production.up.railway.app";

export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || defaultApiBaseUrl
).replace(/\/$/, "");

export function getCallContent(callId, options = {}) {
  return apiFetch(`/calls/${encodeURIComponent(callId)}/content?include_analysis=true`, options);
}

// The four Open Call Aging cards come from their own request so a slow answer never delays the rest of the Dashboard.
export function getOpenCallAging(startDate, endDate, agent = "all", number = "", extension = "") {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate, agent, number, extension });
  return apiFetch(`/api/dashboard/open-call-aging?${query}`);
}

// The five Case Lifecycle Stages cards: {counts: {open, in_transition, lost, dropped, success}, total}; one count per call record.
export function getCaseLifecycle(startDate, endDate, agent = "all", number = "", extension = "") {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate, agent, number, extension });
  return apiFetch(`/api/dashboard/case-lifecycle?${query}`);
}

export function getDashboardSummary(startDate, endDate, agent = "all", number = "", extension = "") {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate, agent, number, extension });
  return apiFetch(`/api/dashboard/summary?${query}`);
}

// The actual enquiry questions behind a Trends figure (one entry per call), for the same period as the report.
// `filters.caseType` / `filters.question` narrow it to one bar or one repeated question; omit both for everything.
export function getCaseTypeTrendQuestions(period, anchor, range, filters = {}) {
  const query = trendsQuery(period, anchor, range);
  if (filters.caseType) query.set("case_type", filters.caseType);
  if (filters.question) query.set("question", filters.question);
  return apiFetch(`/api/dashboard/case-types-trends/questions?${query}`);
}

// Priority Cases: stored RFE / I-130 / I-485 / H4 / H4EAD classifications for the date range. Read-only; never starts an AI analysis.
export function getCaseTypesEq(startDate, endDate) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate });
  return apiFetch(`/api/dashboard/case-types-eq?${query}`);
}

let sessionRedirectStarted = false;
const inFlightReadRequests = new Map();

function clearAuthState() {
  localStorage.removeItem("flintdesk_token");
  localStorage.removeItem("flintdesk_user");
  localStorage.removeItem("flintdesk_active_page");
}

function redirectToLoginOnUnauthorized(response) {
  if (response.status !== 401) return false;

  if (!sessionRedirectStarted) {
    sessionRedirectStarted = true;
    clearAuthState();
    window.location.reload();
  }
  return true;
}

let renewalPromise = null;
async function sessionToken() {
  const token = localStorage.getItem("flintdesk_token");
  if (!token) return null;
  let expires;
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    expires = JSON.parse(atob(payload.padEnd(Math.ceil(payload.length / 4) * 4, "="))).exp;
  } catch { return token; }
  if (!expires || expires * 1000 - Date.now() > 5 * 60_000) return token;
  if (!renewalPromise) {
    renewalPromise = (async () => {
      const response = await fetch(`${API_BASE_URL}/fd-auth/renew`, {
        method: "POST", headers: { Authorization: `Bearer ${token}` },
      });
      // A different tab may have renewed or changed accounts while this request ran.
      if (localStorage.getItem("flintdesk_token") !== token) return localStorage.getItem("flintdesk_token");
      if (redirectToLoginOnUnauthorized(response)) throw new Error("Session expired. Please sign in again.");
      if (!response.ok) throw new Error("Session renewal temporarily unavailable. Please retry.");
      const data = await response.json();
      localStorage.setItem("flintdesk_token", data.access_token);
      return data.access_token;
    })().finally(() => { renewalPromise = null; });
  }
  return renewalPromise;
}

export async function getCallAudioBlobUrl(callId) {
  const token = await sessionToken();
  const response = await fetch(getCallAudioUrl(callId), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (response.status === 401 && localStorage.getItem("flintdesk_token") === token && redirectToLoginOnUnauthorized(response)) throw new Error("Session expired. Please sign in again.");
  if (!response.ok)
    throw new Error(`Unable to load audio (${response.status}).`);
  return URL.createObjectURL(await response.blob());
}

async function performApiFetch(path, options = {}) {
  const token = await sessionToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (response.status === 401 && localStorage.getItem("flintdesk_token") === token && redirectToLoginOnUnauthorized(response)) throw new Error("Session expired. Please sign in again.");
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const rawDetail =
      data?.detail ||
      data?.message ||
      `API request failed (${response.status})`;
    const detail =
      typeof rawDetail === "string"
        ? rawDetail
        : rawDetail?.message
          ? `${rawDetail.message}${
              rawDetail.errors?.length
                ? ` ${rawDetail.errors
                    .slice(0, 3)
                    .map((item) => `Row ${item.row}: ${item.error}`)
                    .join(" ")}`
                : ""
            }`
          : JSON.stringify(rawDetail);
    throw new Error(detail);
  }
  const method = String(options.method || "GET").toUpperCase();
  if (method !== "GET" && /^(\/calls|\/api\/flintdesk|\/fd-assignments)/.test(path)) {
    inFlightReadRequests.clear();
    window.dispatchEvent(new Event("flintdesk-data-changed"));
    if (typeof BroadcastChannel !== "undefined") {
      const channel = new BroadcastChannel("flintdesk-data-changed");
      channel.postMessage({ changed: true });
      channel.close();
    }
  }
  return response.status === 204 ? null : data;
}

// One browser can render multiple widgets/effects that ask for the identical
// endpoint. Share that in-flight read rather than issuing duplicate large
// table queries. Mutations are deliberately never coalesced.
function apiFetch(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  if (method !== "GET") return performApiFetch(path, options);
  const existing = inFlightReadRequests.get(path);
  if (existing) return existing;
  const request = performApiFetch(path, options).finally(() => {
    if (inFlightReadRequests.get(path) === request) inFlightReadRequests.delete(path);
  });
  inFlightReadRequests.set(path, request);
  return request;
}

export async function loginFD(username, password) {
  const response = await fetch(`${API_BASE_URL}/fd-auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(data?.detail || "Invalid credentials.");
  return data;
}

export async function getCallAnalysis(
  startDate,
  endDate,
  number = "",
  extension = "",
  clientId = "",
  targetPage = "calls",
  agent = "",
  callDirection = "",
  page = null,
  pageSize = null,
  clientNumber = "",
  callId = "",
  leadId = "",
  callKind = "",
  reviewStatus = "",
  cursor = null,
  openCases = false,
  actionStatus = "",
  followUpDate = "",
  caseStatus = "",
  lifecycleStage = "",
  agingDependency = "",
  agingDays = 0,
  priorityForm = "",
) {
  const query = new URLSearchParams();
  if (startDate) query.set("start_date", startDate);
  if (endDate) query.set("end_date", endDate);
  if (number) query.set("number", number);
  if (extension) query.set("extension", extension);
  if (clientId) query.set("client_id", clientId);
  if (clientNumber) query.set("client_number", clientNumber);
  if (callId) query.set("call_id", callId);
  if (leadId) query.set("lead_id", leadId);
  if (callKind && callKind !== "all") query.set("call_kind", callKind);
  if (reviewStatus && reviewStatus !== "all") query.set("review_status", reviewStatus);
  if (agent && agent !== "all") query.set("agent", agent);
  if (callDirection) query.set("call_direction", callDirection);
  if (openCases) query.set("open_cases", "true");
  if (actionStatus) query.set("action_status", actionStatus);
  if (caseStatus) query.set("case_status", caseStatus);   // "" = All Case Statuses; "__none__" = not set
  if (lifecycleStage) query.set("lifecycle_stage", lifecycleStage);   // open | in_transition | lost | dropped | success
  // Dashboard dependency popup: "agent" | "other_agent" (the saved AI answer) aged at least `agingDays` days.
  if (agingDependency) { query.set("aging_dependency", agingDependency); if (agingDays) query.set("aging_days", String(agingDays)); }
  // Priority Cases filter: RFE | I-130 | I-485 | H4 | H4EAD, from the stored (confirmed) classifications.
  if (priorityForm) query.set("priority_form", priorityForm);
  // Calendar date (YYYY-MM-DD) matched against the Stage 2 Follow Up Dates entries.
  if (followUpDate) query.set("follow_up_date", followUpDate);
  query.set("target_page", targetPage);
  // Omitting page/pageSize keeps the exact previous behavior -- the backend
  // returns the plain unpaginated array whenever it doesn't see both.
  // Passing both switches the response to { items, pagination }.
  if (page && pageSize) {
    query.set("page", page);
    query.set("page_size", pageSize);
    if (cursor !== null) {
      query.set("cursor_mode", "true");
      if (cursor) query.set("cursor", cursor);
    }
  }
  return apiFetch(`/calls/analysis${query.size ? `?${query}` : ""}`);
}

// Distinct follow-up dates (YYYY-MM-DD, ascending) of the calls the Calls table lists for these
// filters. `filters` uses the same applied values the table request uses.
export async function getFollowUpDates({
  startDate, endDate, number = "", extension = "", clientId = "", agent = "", callDirection = "",
  clientNumber = "", callId = "", leadId = "", callKind = "", reviewStatus = "", openCases = false,
  actionStatus = "", caseStatus = "", lifecycleStage = "", agingDependency = "", priorityForm = "",
}, options = {}) {
  const query = new URLSearchParams({ target_page: "calls" });
  if (startDate) query.set("start_date", startDate);
  if (endDate) query.set("end_date", endDate);
  if (number) query.set("number", number);
  if (extension) query.set("extension", extension);
  if (clientId) query.set("client_id", clientId);
  if (clientNumber) query.set("client_number", clientNumber);
  if (callId) query.set("call_id", callId);
  if (leadId) query.set("lead_id", leadId);
  if (callKind && callKind !== "all") query.set("call_kind", callKind);
  if (reviewStatus && reviewStatus !== "all") query.set("review_status", reviewStatus);
  if (agent && agent !== "all") query.set("agent", agent);
  if (callDirection) query.set("call_direction", callDirection);
  if (openCases) query.set("open_cases", "true");
  if (actionStatus) query.set("action_status", actionStatus);
  if (caseStatus) query.set("case_status", caseStatus);   // "" = All Case Statuses; "__none__" = not set
  if (lifecycleStage) query.set("lifecycle_stage", lifecycleStage);   // open | in_transition | lost | dropped | success
  if (agingDependency) query.set("aging_dependency", agingDependency);
  if (priorityForm) query.set("priority_form", priorityForm);
  const result = await apiFetch(`/calls/analysis/follow-up-dates?${query}`, options);
  return Array.isArray(result?.dates) ? result.dates : [];
}

export const getCallSummaries = (callIds) =>
  apiFetch("/calls/analysis/summaries", {
    method: "POST",
    body: JSON.stringify({ call_ids: callIds }),
  });

export async function getDashboardCalls(
  startDate,
  endDate,
  number = "",
  extension = "",
) {
  const query = new URLSearchParams({
    dashboard: "true",
    target_page: "dashboard",
  });
  if (startDate) query.set("start_date", startDate);
  if (endDate) query.set("end_date", endDate);
  if (number) query.set("number", number);
  if (extension) query.set("extension", extension);
  return apiFetch(`/calls/analysis?${query}`);
}

export async function getDashboardPerformanceAnalysis(startDate, endDate) {
  const query = new URLSearchParams();
  if (startDate) query.set("start_date", startDate);
  if (endDate) query.set("end_date", endDate);
  return apiFetch(
    `/calls/dashboard/performance-analysis${query.size ? `?${query}` : ""}`,
  );
}

export async function getDashboardTimeDuration(
  startDate,
  endDate,
  agentIds = [],
) {
  const query = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
  });
  agentIds.forEach((agentId) => query.append("agent_ids", agentId));
  return apiFetch(`/api/dashboard/time-duration?${query}`);
}

// Maps the current FastAPI /calls/analysis response into the compact shape
// expected by the FlintDesk dashboard. The original row is retained in `raw`.
export async function getRecentCalls(startDate, endDate) {
  const rows = await getCallAnalysis(startDate, endDate);
  return rows.slice(0, 10).map((row) => ({
    id: row.id || row.call_id || row.call_reference,
    name: row.client_name || "Not Provided",
    phone: row.phone_number || row.client_phone_number || "Not Provided",
    caseType: row.case_type || "Not Provided",
    direction: row.call_direction || row.call_type || "Not Provided",
    urgency: row.urgency || "None",
    status: row.action_status || "Needs Review",
    time: row.call_time || row.call_recording_date || "-",
    raw: row,
  }));
}

export async function getCalls(
  startDate,
  endDate,
  number = "",
  extension = "",
  clientId = "",
  targetPage = "calls",
  agent = "",
  callDirection = "",
  onlyUnanalyzed = false,
  clientNumber = "",
  callId = "",
  leadId = "",
  openCases = false,
  actionStatus = "",
  caseStatus = "",
  lifecycleStage = "",
) {
  const query = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
  });
  if (number) query.set("number", number);
  if (extension) query.set("extension", extension);
  if (clientId) query.set("client_id", clientId);
  if (agent && agent !== "all") query.set("agent", agent);
  if (callDirection) query.set("call_direction", callDirection);
  if (onlyUnanalyzed) query.set("only_unanalyzed", "true");
  if (clientNumber) query.set("client_number", clientNumber);
  if (callId) query.set("call_id", callId);
  if (leadId) query.set("lead_id", leadId);
  if (openCases) query.set("open_cases", "true");
  if (actionStatus) query.set("action_status", actionStatus);
  if (caseStatus) query.set("case_status", caseStatus);   // "" = All Case Statuses; "__none__" = not set
  if (lifecycleStage) query.set("lifecycle_stage", lifecycleStage);   // open | in_transition | lost | dropped | success
  query.set("target_page", targetPage);
  // Trailing slash avoids a 307 redirect, which breaks CORS preflight for cross-origin requests.
  return apiFetch(`/calls/?${query}`);
}

export async function getTeamPerformanceData(
  startDate,
  endDate,
  number = "",
  extension = "",
) {
  const query = new URLSearchParams();
  if (startDate) query.set("start_date", startDate);
  if (endDate) query.set("end_date", endDate);
  if (number) query.set("number", number);
  if (extension) query.set("extension", extension);
  return apiFetch(
    `/calls/dashboard/team-performance${query.size ? `?${query}` : ""}`,
  );
}

export function getCallAudioUrl(callId) {
  return `${API_BASE_URL}/calls/audio?call_id=${encodeURIComponent(callId)}`;
}

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
function _buildCallRecordsWithTasks(rows, calls, tasks, leads, { includeUnmatchedTasks = true, smLeadCalls = [] } = {}) {
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
  // SM_Lead label: the exact outbound call that turned the SM Leads row green
  // (see sm_lead_call_links in routes/flintdesk/sm_leads.py).
  const smLeadByCallId = new Map((smLeadCalls || []).map((link) => [link.call_id, link]));
  const callRecordsWithTasks = callRecords.map((record) => {
    const directTask = tasksByCallId.get(record.callId);
    const callbackTask = missedCallByCallbackCallId.get(record.callId);
    const lead = leadByCallId.get(record.callId);
    const leadTask = lead && { id: lead.id, type: "lead", label: LEAD_TASK_LABEL };
    const smLead = smLeadByCallId.get(record.callId);
    const smLeadTask = smLead && { id: smLead.id, type: "sm_lead", label: "SM_Lead" };
    const linkedTasks = [
      directTask && { id: directTask.id, type: directTask.task_type, label: TASK_LABELS[directTask.task_type] },
      // A callback can resolve a Missed Call and also later be selected for a
      // Lead.  It remains evidence for the Missed Calls workflow, but the
      // Calls page must give the explicit Lead association priority so one
      // call row is never labelled as both task types.
      !leadTask && callbackTask && { id: callbackTask.id, type: callbackTask.task_type, label: TASK_LABELS[callbackTask.task_type] },
      leadTask,
      smLeadTask,
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

// Maps /calls/analysis rows into the shape used by the FlintDesk Calls table.
// Audio/transcript live on the `calls` table (not call_analysis_results), so
// they're fetched separately here and merged in by call_id.
export async function getCallsList(
  startDate,
  endDate,
  number = "",
  extension = "",
  clientId = "",
  agent = "",
  callDirection = "",
  requireComplete = false,
) {
  const [analysisResult, callsResult, tasksResult, leadsResult, smLeadCallsResult] = await Promise.allSettled([
    getCallAnalysis(startDate, endDate, number, extension, clientId, "calls", agent, callDirection),
    getCalls(startDate, endDate, number, extension, clientId, "calls", agent, callDirection),
    getTasks({ start_date: startDate, end_date: endDate, include_callback: true }),
    // dashboard: true surfaces every agent's leads here, matching the Calls
    // page's existing "same call population for every role" scope (see
    // _fd_assignment_numbers in routes/calls.py) -- not just the viewer's own.
    getLeads({ start_date: startDate, end_date: endDate, dashboard: true }),
    getSMLeadCallLinks(startDate, endDate),
  ]);
  if (requireComplete) {
    const failed = [analysisResult, callsResult, tasksResult, leadsResult].find(result => result.status === "rejected");
    if (failed) throw failed.reason;
  }
  // Analysis is supplementary to the Calls table. If its endpoint is briefly
  // unavailable, retain the source CDR rows instead of reporting zero calls.
  const rows = analysisResult.status === "fulfilled" ? analysisResult.value : [];
  const calls = callsResult.status === "fulfilled" ? callsResult.value : [];
  const tasks = tasksResult.status === "fulfilled" ? tasksResult.value : [];
  const leads = leadsResult.status === "fulfilled" ? leadsResult.value : [];
  // The label is supplementary: a failed lookup must not break the table.
  const smLeadCalls = smLeadCallsResult.status === "fulfilled" ? smLeadCallsResult.value : [];
  return _buildCallRecordsWithTasks(rows, calls, tasks, leads, { smLeadCalls });
}

// "All Logs of Phone Number" popup: every call log for one client number,
// optionally bounded by dates. Rows are shaped by the same
// _buildCallRecordsWithTasks the Calls table uses, so the popup's table and
// its row actions are the Calls page's own. Task/Lead/SM_Lead labels are
// supplementary (their endpoints are date-windowed), so they are looked up
// in windows over the span of the returned calls and skipped on failure.
const LABEL_WINDOW_DAYS = 90;
const MAX_LABEL_WINDOWS = 8;
export async function getCallsByClientPhone(phone, fromDate = "", toDate = "") {
  const query = new URLSearchParams({ client_phone: phone });
  if (fromDate) query.set("from_date", fromDate);
  if (toDate) query.set("to_date", toDate);
  const { items = [], pending_calls: pendingCalls = [], missed_calls_count: missedCallsCount = 0 } = await apiFetch(`/calls/analysis/by-client-phone?${query}`);
  let summarizedItems = items;
  try {
    const summaries = await getCallSummaries(items.map((row) => row.call_id).filter(Boolean));
    summarizedItems = items.map((row) => ({ ...row, ...(summaries[String(row.call_id)] || {}) }));
  } catch {
    // Summary previews are optional; opening a summary still loads call content.
  }
  const day = (value) => String(value || "").slice(0, 10);
  const dates = [
    ...summarizedItems.map((row) => day(row.call_recording_date_normalized || row.call_recording_date || row.call_date)),
    ...pendingCalls.map((call) => day(call.call_date)),
  ].filter((value) => /^\d{4}-\d{2}-\d{2}$/.test(value)).sort();
  let tasks = [];
  let leads = [];
  let smLeadCalls = [];
  if (dates.length) {
    const windows = [];
    for (let cursor = new Date(`${dates[0]}T00:00:00Z`); windows.length < MAX_LABEL_WINDOWS; ) {
      const end = new Date(cursor.getTime() + (LABEL_WINDOW_DAYS - 1) * 86400000);
      windows.push([cursor.toISOString().slice(0, 10), end.toISOString().slice(0, 10)]);
      if (end.toISOString().slice(0, 10) >= dates[dates.length - 1]) break;
      cursor = new Date(end.getTime() + 86400000);
    }
    const results = await Promise.allSettled(windows.flatMap(([start, end]) => [
      getTasks({ start_date: start, end_date: end, include_callback: true }),
      getLeads({ start_date: start, end_date: end, dashboard: true }),
      getSMLeadCallLinks(start, end),
    ]));
    const pick = (offset) => results.filter((_, index) => index % 3 === offset)
      .flatMap((result) => (result.status === "fulfilled" ? result.value : []));
    tasks = pick(0);
    leads = pick(1);
    smLeadCalls = pick(2);
  }
  const records = _buildCallRecordsWithTasks(summarizedItems, pendingCalls, tasks, leads, { includeUnmatchedTasks: false, smLeadCalls });
  // missedCallsCount: entries for this number on the Missed Calls page.
  return { records, missedCallsCount: Number(missedCallsCount) || 0 };
}

// Calls-table rows (agent, client / lead ids, extension, direction, duration, stage values ...) for analysis records, built by the
// very function the Calls page uses, so another table that repeats the Call Records columns shows identical values.
export function buildCallRecordsFromAnalysis(rows) {
  return _buildCallRecordsWithTasks(rows, [], [], [], { includeUnmatchedTasks: false });
}

// Paginated sibling of getCallsList, for the Calls page's primary table.
// Requests only the page being viewed from /calls/analysis instead of the
// whole matching dataset, and skips the separate /calls/ fetch entirely --
// the paginated analysis rows already carry transcript/audio/billsec (see
// routes/calls.py serialize_analysis_rows) since that Call data is already
// loaded server-side to build the response anyway.
//
// The analysis endpoint is paginated, while the small companion request
// fetches only raw CDRs that are still awaiting analysis. This preserves
// pagination for processed calls without hiding new-extension calls.
export async function getCallsListPaged(
  startDate,
  endDate,
  number = "",
  extension = "",
  clientId = "",
  agent = "",
  callDirection = "",
  page = 1,
  pageSize = 100,
  clientNumber = "",
  callId = "",
  leadId = "",
  callKind = "",
  reviewStatus = "",
  cursor = null,
  openCases = false,
  actionStatus = "",
  followUpDate = "",
  caseStatus = "",
  lifecycleStage = "",
  agingDependency = "",
  agingDays = 0,
  priorityForm = "",
) {
  const [analysisResult, pendingCallsResult, tasksResult, leadsResult, smLeadCallsResult] = await Promise.allSettled([
    getCallAnalysis(startDate, endDate, number, extension, clientId, "calls", agent, callDirection, page, pageSize, clientNumber, callId, leadId, callKind, reviewStatus, cursor, openCases, actionStatus, followUpDate, caseStatus, lifecycleStage, agingDependency, agingDays, priorityForm),
    // Calls with no analysis yet cannot have a follow-up date, a saved AI dependency or a priority classification, so none are listed under those filters.
    followUpDate || agingDependency || priorityForm ? Promise.resolve([]) : getCalls(
      startDate, endDate, number, extension, clientId, "calls", agent,
      callDirection, true, clientNumber, callId, leadId, openCases, actionStatus, caseStatus, lifecycleStage,
    ),
    getTasks({ start_date: startDate, end_date: endDate, include_callback: true }),
    getLeads({ start_date: startDate, end_date: endDate, dashboard: true }),
    getSMLeadCallLinks(startDate, endDate),
  ]);
  if (analysisResult.status === "rejected") throw analysisResult.reason;
  const { items, pagination } = analysisResult.value;
  // Keep the deployed /calls/analysis response fast and unchanged. Summary
  // text is fetched once for this visible page only, just like Missed Calls
  // displays it, so it cannot break the primary Calls table query.
  let summarizedItems = items;
  try {
    const summaries = await getCallSummaries(items.map((row) => row.call_id).filter(Boolean));
    summarizedItems = items.map((row) => ({ ...row, ...(summaries[String(row.call_id)] || {}) }));
  } catch {
    // The main Calls response remains usable if this optional preview request
    // is temporarily unavailable; opening a summary still uses call content.
  }
  const pendingCalls = pendingCallsResult.status === "fulfilled" ? pendingCallsResult.value : [];
  const tasks = tasksResult.status === "fulfilled" ? tasksResult.value : [];
  const leads = leadsResult.status === "fulfilled" ? leadsResult.value : [];
  const smLeadCalls = smLeadCallsResult.status === "fulfilled" ? smLeadCallsResult.value : [];
  // Still-unanalysed calls are one range-wide list, so list them on the first
  // page only; adding them to every page repeated the same rows 34 times.
  const records = _buildCallRecordsWithTasks(summarizedItems, page === 1 ? pendingCalls : [], tasks, leads, {
    // Task rows are not call records with a Case/Action Status, so they are left out of a filtered view.
    includeUnmatchedTasks: page === 1 && !openCases && !actionStatus && !caseStatus && !lifecycleStage && !agingDependency && !priorityForm,
    smLeadCalls,
  });
  // `records` is this page's analysed rows plus every still-unanalysed call
  // and any unmatched task rows, so its length is not the number of calls in
  // the selected range. Report the real total separately.
  const analysedIds = new Set(summarizedItems.map((row) => row.call_id));
  const pendingCount = pendingCalls.filter((call) => !analysedIds.has(call.call_id)).length;
  return {
    records,
    pagination: pagination ? { ...pagination, pending_calls: pendingCount } : pagination,
  };
}

export async function getDashboardCallsList(
  startDate,
  endDate,
  number = "",
  extension = "",
) {
  const [analysisResult, callsResult] = await Promise.allSettled([
    getDashboardCalls(startDate, endDate, number, extension),
    getCalls(startDate, endDate, number, extension, "", "dashboard"),
  ]);
  // Total Calls is defined from the calls table and must remain available if
  // analysis data is delayed or its endpoint has a transient failure.
  const rows = analysisResult.status === "fulfilled" ? analysisResult.value : [];
  const calls = callsResult.status === "fulfilled" ? callsResult.value : [];
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

// Stage 2 intentionally uses the existing /calls/manual-fields/{analysis_id}
// endpoint from routes/calls.py, not the FlintDesk-specific route family.
export async function updateManualFields(analysisId, fields) {
  return apiFetch(`/calls/manual-fields/${analysisId}`, {
    method: "PATCH",
    body: JSON.stringify(fields),
  });
}

export const scoreManualData = (analysisId) =>
  apiFetch(
    `/calls/analysis/${encodeURIComponent(analysisId)}/manual-data-score`,
    { method: "POST" },
  );

// FlintDesk-specific call read/update API. Stage 1 uses the ai-generated fd_a_* values
// and Stage 3/4 manual fields are posted through this route family.
export async function getFlintDeskCall(callId) {
  return apiFetch(`/api/flintdesk/calls/${encodeURIComponent(callId)}`);
}

export async function getFlintDeskClientHistory(clientId) {
  return apiFetch(
    `/api/flintdesk/clients/${encodeURIComponent(clientId)}/calls`,
  );
}

export async function updateClientProfile(clientId, fields) {
  return apiFetch(`/api/flintdesk/clients/${encodeURIComponent(clientId)}`, {
    method: "PATCH",
    body: JSON.stringify(fields),
  });
}

export async function analyseFlintDeskCall(callId, { requestId } = {}) {
  const query = requestId
    ? `?reanalyse=true&request_id=${encodeURIComponent(requestId)}`
    : "";
  return apiFetch(
    `/api/flintdesk/calls/${encodeURIComponent(callId)}/analyse${query}`,
    {
      method: "POST",
    },
  );
}

export async function updateFlintDeskManualFields(callId, fields) {
  return apiFetch(
    `/api/flintdesk/calls/${encodeURIComponent(callId)}/manual-fields`,
    {
      method: "PATCH",
      body: JSON.stringify(fields),
    },
  );
}

// Bulk Add/Import for the Calls page -- each record must include a "call_id"
// (UUID or AF# call_reference) identifying an existing call, plus any subset
// of the same Stage 1-4 fields updateFlintDeskManualFields already accepts.
export async function importCalls(records) {
  return apiFetch("/api/flintdesk/calls/import", {
    method: "POST",
    body: JSON.stringify({ records }),
  });
}

export async function updateFlintDeskCallField(callId, field, value, expectedValue) {
  return apiFetch(`/api/flintdesk/calls/${encodeURIComponent(callId)}/field`, {
    method: "PATCH",
    body: JSON.stringify({ field, value, expected_value: expectedValue }),
  });
}

// Generic single-field updater used by the Calls table for inline edits.
// Wraps updateFlintDeskManualFields so callers don't need to build the
// fields object themselves.
export async function updateCallField(callId, field, value, expectedValue) {
  // Case Closing still uses the established multi-field endpoint. Calls-page
  // edits pass an expected value and therefore use the fast conflict-aware
  // endpoint below.
  if (expectedValue === undefined) {
    return updateFlintDeskManualFields(callId, { [field]: value });
  }
  return updateFlintDeskCallField(callId, field, value, expectedValue);
}

// Mark a case as completed in the Case Closing workflow.
// Uses updateManualFields to persist case_closing_completed flag to backend.
export async function markCaseAsCompleted(analysisId) {
  return updateManualFields(analysisId, { case_closing_completed: true });
}

export async function uploadFlintDeskAttachment(
  callId,
  file,
  target = "fd_m_attachment",
) {
  const formData = new FormData();
  formData.append("file", file);
  const token = localStorage.getItem("flintdesk_token");

  const response = await fetch(
    `${API_BASE_URL}/api/flintdesk/calls/${encodeURIComponent(callId)}/attachment?target=${encodeURIComponent(target)}`,
    {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: formData,
    },
  );
  if (response.status === 401 && localStorage.getItem("flintdesk_token") === token && redirectToLoginOnUnauthorized(response)) throw new Error("Session expired. Please sign in again.");

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      data?.detail || `Attachment upload failed (${response.status})`;
    throw new Error(detail);
  }

  return data;
}

// Attachments are stored in a private Supabase Storage bucket, so viewing an
// existing one requires a fresh short-lived signed URL rather than a fixed link.
export async function getFlintDeskAttachmentUrl(
  callId,
  target = "fd_m_attachment",
) {
  const { url } = await apiFetch(
    `/api/flintdesk/calls/${encodeURIComponent(callId)}/attachment-url?target=${encodeURIComponent(target)}`,
  );
  return url;
}

export async function deleteFlintDeskAttachment(
  callId,
  target = "fd_m_attachment",
) {
  return apiFetch(
    `/api/flintdesk/calls/${encodeURIComponent(callId)}/attachment?target=${encodeURIComponent(target)}`,
    {
      method: "DELETE",
    },
  );
}

export async function getFdPrompts(includeHistory = false) {
  return apiFetch(`/flintdesk/fd-prompts?include_history=${includeHistory ? "true" : "false"}`);
}

export async function saveFdPrompt(promptKey, systemPrompt, userPromptTemplate) {
  return apiFetch(`/flintdesk/fd-prompts/${encodeURIComponent(promptKey)}`, {
    method: "PUT",
    body: JSON.stringify({ system_prompt: systemPrompt, user_prompt_template: userPromptTemplate }),
  });
}

export async function rollbackFdPrompt(promptKey, version) {
  return apiFetch(`/flintdesk/fd-prompts/${encodeURIComponent(promptKey)}/rollback/${version}`, { method: "POST" });
}

export async function regenerateStage2(callId, replaceManual = false) {
  return apiFetch(`/api/flintdesk/calls/${encodeURIComponent(callId)}/regenerate-stage2?replace_manual=${replaceManual}`, { method: "POST" });
}

// Sent with the server's confirm result when a closure notification was confirmed (see Calls.jsx).
export const CLOSURE_ROWS_CHANGED_EVENT = "flintdesk-closure-rows-changed";

// Persistent cross-agent case-closure notifications (only ever the signed-in user's own).
export async function getPendingClosureNotifications() {
  const result = await apiFetch("/flintdesk/case-closures/notifications");
  return Array.isArray(result) ? result : [];
}

export async function confirmClosureNotification(id) {
  const result = await apiFetch(`/flintdesk/case-closures/notifications/${encodeURIComponent(id)}/confirm`, { method: "POST" });
  inFlightReadRequests.clear(); // a read that started before the confirmation must not be reused afterwards
  // Rows changed by this confirmation: a mounted Calls table shows them at once (no reload, filters untouched).
  window.dispatchEvent(new CustomEvent(CLOSURE_ROWS_CHANGED_EVENT, { detail: result }));
  window.dispatchEvent(new Event("flintdesk-data-changed")); // the Dashboard and other pages re-read open counts
  return result;
}

export async function dismissClosureNotification(id) {
  return apiFetch(`/flintdesk/case-closures/notifications/${encodeURIComponent(id)}/dismiss`, { method: "POST" });
}

// The signed-in agent's OWN other open logs for the same client and case context as `callId`, just closed by them.
// { closure_id, closure_status, client_name, client_number, case_context, logs: [...] }; logs is [] when nothing matches.
export async function getOwnRelatedLogs(callId) {
  return apiFetch(`/flintdesk/case-closures/own-related?call_id=${encodeURIComponent(callId)}`);
}

// Close the chosen related logs with the closure's status. The server re-validates every log and reports
// { state, updated: [...], skipped: [...], closure_status, closure }.
export async function closeOwnRelatedLogs(closureId, analysisIds) {
  const result = await apiFetch(`/flintdesk/case-closures/${encodeURIComponent(closureId)}/own-related/confirm`, {
    method: "POST",
    body: JSON.stringify({ analysis_ids: analysisIds }),
  });
  inFlightReadRequests.clear();
  window.dispatchEvent(new Event("flintdesk-data-changed")); // the Dashboard and other pages re-read open counts
  return result;
}

export async function getSyncFilters() {
  return apiFetch("/flintdesk/sync-filters");
}

export async function addSyncFilter(filterType, value, parentNumber = null) {
  return apiFetch("/flintdesk/sync-filters", {
    method: "POST",
    body: JSON.stringify({
      filter_type: filterType,
      value,
      parent_number: parentNumber,
    }),
  });
}

export async function deleteSyncFilter(filterId) {
  return apiFetch(`/flintdesk/sync-filters/${encodeURIComponent(filterId)}`, {
    method: "DELETE",
  });
}

export async function updateSyncFilter(filterId, value) {
  return apiFetch(`/flintdesk/sync-filters/${encodeURIComponent(filterId)}`, {
    method: "PATCH",
    body: JSON.stringify({ value }),
  });
}

// The signed-in user's own pending counts per Assigning the Calls tab: { counts: { leads, "follow-ups", "sm-leads", general, "missed-calls" } }.
export function getAssignmentCounts() {
  return apiFetch("/api/flintdesk/assignment-counts");
}

// Follow Ups (Assigning the Calls): Success PC calls with their Team Lead Fills data. The server returns an agent only the calls
// submitted to them, and only Admin / Team Lead / SME may save fields or submit.
export function getFollowUps(startDate, endDate, agent = "all") {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate });
  if (agent && agent !== "all") query.set("agent", agent);
  return apiFetch(`/flintdesk/follow-ups?${query}`);
}

export function saveFollowUpFields(callId, updates) {
  return apiFetch(`/flintdesk/follow-ups/${encodeURIComponent(callId)}`, { method: "PATCH", body: JSON.stringify(updates) });
}

export function submitFollowUp(callId) {
  return apiFetch(`/flintdesk/follow-ups/${encodeURIComponent(callId)}/submit`, { method: "POST" });
}

export async function getFDUsers() {
  return apiFetch("/fd-users");
}

export async function getDashboardFrontDeskAgents() {
  return apiFetch("/fd-users/front-desk-agents");
}

export async function addFDUser(name, email, password, role = "agent") {
  return apiFetch("/fd-users", {
    method: "POST",
    body: JSON.stringify({ name, email, password, role }),
  });
}

export async function deleteFDUser(userId) {
  return apiFetch(`/fd-users/${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });
}

export async function updateFDUser(userId, { name, email, password = "" }) {
  return apiFetch(`/fd-users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    body: JSON.stringify({ name, email, ...(password ? { password } : {}) }),
  });
}

export async function getTodayAssignments() {
  return apiFetch("/fd-assignments/today");
}

export async function assignExtension(extension, agentId) {
  return apiFetch("/fd-assignments", {
    method: "POST",
    body: JSON.stringify({ extension, agent_id: agentId }),
  });
}

export async function deleteAssignment(assignmentId) {
  return apiFetch(`/fd-assignments/${encodeURIComponent(assignmentId)}`, {
    method: "DELETE",
  });
}

export async function getMyAssignment() {
  return apiFetch("/fd-assignments/me");
}

export async function getDashboardTaskSummary(startDate, endDate, agent = "") {
  const query = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
  });
  if (agent && agent !== "all") query.set("agent", agent);
  return apiFetch(`/api/flintdesk/tasks/dashboard-summary?${query}`);
}

export async function getLeads(filters = {}) {
  const query = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value && value !== "all") query.set(key, value);
  });
  if (filters.page && filters.page_size) {
    return apiFetch(`/api/flintdesk/leads?${query}`);
  }
  // Existing Leads filters and Dashboard totals use the complete date range.
  // Preserve that contract while bounding each DB enrichment request, with
  // one page in flight at a time. Never display a partial aggregate as final.
  const rows = [];
  const pageSize = 200;
  query.set("page_size", String(pageSize));
  for (let page = 1; ; page += 1) {
    query.set("page", String(page));
    const batch = await apiFetch(`/api/flintdesk/leads?${query}`);
    rows.push(...batch);
    if (batch.length < pageSize) return rows;
  }
}

export async function getLeadExtensions() {
  return apiFetch("/api/flintdesk/leads/extensions");
}

export async function importLeads(records, ownerId = "") {
  return apiFetch("/api/flintdesk/leads/import", {
    method: "POST",
    body: JSON.stringify({
      records: records.map((record) =>
        ownerId ? { ...record, owner_id: ownerId } : record,
      ),
    }),
  });
}

export async function getSMLeads(filters = {}) {
  const query = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value) query.set(key, value);
  });
  return apiFetch(`/api/flintdesk/sm-leads?${query}`);
}

export async function getSMLeadCallLinks(startDate, endDate) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate });
  return apiFetch(`/api/flintdesk/sm-leads/call-links?${query}`);
}

export async function importSMLeads(records) {
  return apiFetch("/api/flintdesk/sm-leads/import", {
    method: "POST",
    body: JSON.stringify({ records }),
  });
}

export async function submitSMLeads(ids, ownerIds = {}) {
  return apiFetch("/api/flintdesk/sm-leads/submit", {
    method: "POST",
    body: JSON.stringify({ ids, owner_ids: ownerIds }),
  });
}

export async function getCallUpdates(startDate, endDate) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate });
  return apiFetch(`/api/flintdesk/call-updates?${query}`);
}

export async function saveCallUpdateEntry({ entryDate, agentId, metric, value, note = "" }) {
  return apiFetch("/api/flintdesk/call-updates/manual", {
    method: "PUT",
    body: JSON.stringify({ entry_date: entryDate, agent_id: agentId, metric, value, note }),
  });
}

export async function deleteSMLeads(ids) {
  return apiFetch("/api/flintdesk/sm-leads/bulk", {
    method: "DELETE",
    body: JSON.stringify({ ids }),
  });
}

export async function analyseLeads() {
  return apiFetch("/api/flintdesk/leads/analyse", { method: "POST" });
}

export async function analyseLead(leadId) {
  return apiFetch(
    `/api/flintdesk/leads/${encodeURIComponent(leadId)}/analyse`,
    { method: "POST" },
  );
}

export async function deleteLeads(ids) {
  return apiFetch("/api/flintdesk/leads/bulk", {
    method: "DELETE",
    body: JSON.stringify({ ids }),
  });
}

export async function updateLeadField(leadId, field, value) {
  return apiFetch(`/api/flintdesk/leads/${encodeURIComponent(leadId)}`, {
    method: "PATCH",
    body: JSON.stringify({ field, value }),
  });
}
export const submitLeads = (ids, ownerIds = {}) =>
  apiFetch("/api/flintdesk/leads/submit", {
    method: "POST",
    body: JSON.stringify({ ids, owner_ids: ownerIds }),
  });
export const getTasks = (filters = {}) => {
  const q = new URLSearchParams(Object.entries(filters).filter(([, v]) => v));
  return apiFetch(`/api/flintdesk/tasks${q.size ? `?${q}` : ""}`);
};
export const saveTask = (task, payload) =>
  apiFetch(`/api/flintdesk/tasks${task?.id ? `/${task.id}` : ""}`, {
    method: task?.id ? "PATCH" : "POST",
    body: JSON.stringify(payload),
  });
export const importTasks = (task_type, records) =>
  apiFetch("/api/flintdesk/tasks/import", {
    method: "POST",
    body: JSON.stringify({ task_type, records }),
  });
export const deleteTasks = (taskType, ids) =>
  apiFetch("/api/flintdesk/tasks/bulk", {
    method: "DELETE",
    body: JSON.stringify({ task_type: taskType, ids }),
  });
export const assignCallTasks = (taskType, ids, ownerId) =>
  apiFetch("/api/flintdesk/tasks/assign", {
    method: "POST",
    body: JSON.stringify({ task_type: taskType, ids, owner_id: ownerId }),
  });
export const getGeneralTaskOwners = () =>
  apiFetch("/api/flintdesk/tasks/owners");
export const getMyTaskNotifications = (status = "all") =>
  apiFetch(`/api/flintdesk/tasks/notifications${status && status !== "all" ? `?status=${encodeURIComponent(status)}` : ""}`);
export const getTaskNotificationSummary = () =>
  apiFetch("/api/flintdesk/tasks/notifications/summary");
export const markAllTaskNotificationsRead = () =>
  apiFetch("/api/flintdesk/tasks/notifications/read-all", { method: "PATCH" });
export const markTaskNotificationRead = (notificationId) =>
  apiFetch(
    `/api/flintdesk/tasks/notifications/${encodeURIComponent(notificationId)}/read`,
    { method: "PATCH" },
  );
export const getTodos = (status = "") =>
  apiFetch(
    `/api/flintdesk/todos${status ? `?status=${encodeURIComponent(status)}` : ""}`,
  );
export const createTodo = (payload) =>
  apiFetch("/api/flintdesk/todos", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const updateTodoStatus = (todoId, status) =>
  apiFetch(`/api/flintdesk/todos/${encodeURIComponent(todoId)}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
export const updateTodo = (todoId, payload) =>
  apiFetch(`/api/flintdesk/todos/${encodeURIComponent(todoId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
export const deleteTodo = (todoId) =>
  apiFetch(`/api/flintdesk/todos/${encodeURIComponent(todoId)}`, {
    method: "DELETE",
  });
export const getVisaUpdates = (page = 1, pageSize = 5) =>
  apiFetch(`/api/flintdesk/visa-updates?page=${page}&page_size=${pageSize}`);
export const getVisaUpdateCounts = () =>
  apiFetch("/api/flintdesk/visa-updates/counts");
export const getVisaUpdate = (updateId) =>
  apiFetch(`/api/flintdesk/visa-updates/${encodeURIComponent(updateId)}`);
export const createVisaUpdate = (payload) =>
  apiFetch("/api/flintdesk/visa-updates", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const acknowledgeVisaUpdate = (updateId, answers) =>
  apiFetch(
    `/api/flintdesk/visa-updates/${encodeURIComponent(updateId)}/acknowledge`,
    { method: "POST", body: JSON.stringify({ answers }) },
  );
export const deleteVisaUpdate = (updateId) =>
  apiFetch(`/api/flintdesk/visa-updates/${encodeURIComponent(updateId)}`, {
    method: "DELETE",
  });

// ============================================
// Dependencies API Functions
// ============================================

export async function getDependencies(
  pending = false,
  completed = false,
  callId = "",
  clientId = "",
  leadId = "",
) {
  const params = new URLSearchParams();
  if (pending) params.append("pending", "true");
  if (completed) params.append("completed", "true");
  if (callId) params.append("call_id", callId);
  if (clientId) params.append("client_id", clientId);
  if (leadId) params.append("lead_id", leadId);
  return apiFetch(`/api/flintdesk/dependencies/?${params}`);
}

export async function getLeadDetails(leadId) {
  return apiFetch(
    `/api/flintdesk/leads/by-public-id/${encodeURIComponent(leadId)}`,
  );
}

export async function getDashboardDependencySummary(startDate, endDate) {
  const params = new URLSearchParams();
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  return apiFetch(
    `/api/flintdesk/dependencies/dashboard-summary${params.size ? `?${params}` : ""}`,
  );
}

export async function createDependency(payload) {
  return apiFetch("/api/flintdesk/dependencies/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateDependency(dependencyId, payload) {
  return apiFetch(
    `/api/flintdesk/dependencies/${encodeURIComponent(dependencyId)}`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    },
  );
}

export async function approveDependency(dependencyId, payload) {
  // Approval is a normal persisted PATCH so the backend can atomically enforce
  // reviewer RBAC and save both SME fields with the approved status.
  return updateDependency(dependencyId, { ...payload, status: "approved" });
}

export async function deleteDependency(dependencyId) {
  return apiFetch(
    `/api/flintdesk/dependencies/${encodeURIComponent(dependencyId)}`,
    { method: "DELETE" },
  );
}

export async function getCallSummaryByIdAndDate(callId, clientEnquiryDate) {
  const params = new URLSearchParams({
    call_id: callId,
    client_enquiry_date: clientEnquiryDate,
  });
  return apiFetch(`/api/flintdesk/dependencies/analyse/call-summary?${params}`);
}

// Admin-only daily call-count snapshots (see AutoFlint_Backend/services/call_count_snapshot.py).
export async function getCallCountHistory(days = 45) {
  return apiFetch(`/api/flintdesk/call-count-reconciliation?days=${days}`);
}

export async function getCallCountReconciliation(businessDate) {
  return apiFetch(`/api/flintdesk/call-count-reconciliation/${encodeURIComponent(businessDate)}`);
}

export async function getCallCountAffectedCalls(businessDate, { agent, extension } = {}) {
  const query = new URLSearchParams();
  if (agent) query.set("agent", agent);
  if (extension) query.set("extension", extension);
  const suffix = query.toString() ? `?${query}` : "";
  return apiFetch(`/api/flintdesk/call-count-reconciliation/${encodeURIComponent(businessDate)}/affected-calls${suffix}`);
}

export async function retryCallCountSnapshot(businessDate) {
  return apiFetch(`/api/flintdesk/call-count-reconciliation/${encodeURIComponent(businessDate)}/snapshot`, { method: "POST" });
}

// Case Types + EQ trends: verified report for a Daily / Weekly / Monthly / Custom Central-time period, and the AI
// wording of that same report. `anchor` (YYYY-MM-DD, any date inside the period) is omitted for "today in Central
// time"; `range` ({ start, end }) is used only for the custom period.
function trendsQuery(period, anchor, range) {
  const query = new URLSearchParams({ period });
  if (period === "custom" && range) {
    query.set("start_date", range.start);
    query.set("end_date", range.end);
  } else if (anchor) query.set("anchor", anchor);
  return query;
}
export const getCaseTypeTrends = (period, anchor, range) => apiFetch(`/api/dashboard/case-types-trends?${trendsQuery(period, anchor, range)}`);
export const getCaseTypeTrendInsights = (period, anchor, range) => apiFetch(`/api/dashboard/case-types-trends/insights?${trendsQuery(period, anchor, range)}`);
