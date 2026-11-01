function resolveApiBaseUrl() {
  const envUrl = (import.meta.env.VITE_API_BASE_URL || "").trim();

  if (envUrl) {
    // Browsers cannot reach 0.0.0.0; normalize if accidentally provided.
    return envUrl.replace("0.0.0.0", "localhost").replace(/\/$/, "");
  }

  if (typeof window !== "undefined") {
    const isLocal = ["localhost", "127.0.0.1"].includes(window.location.hostname);
    if (isLocal) {
      return "http://localhost:8000";
    }
  }

  return "https://autoflint-backend.onrender.com";
}

export const API_BASE_URL = resolveApiBaseUrl();

function createApiError(message, status) {
  const error = new Error(message);
  error.status = status;
  return error;
}

function getToken() {
  return localStorage.getItem("autoflint_token");
}

async function apiFetch(path, options = {}) {
  const token = getToken();
  const controller = new AbortController();
  // The backend's DB pool wait (20s) plus its own query statement timeout
  // (30s) can legitimately take up to ~50s under load. A 20s client abort
  // was firing "Request timed out" before the server ever got a chance to
  // finish or fail, so this needs to stay comfortably above that ceiling.
  const timeoutId = setTimeout(() => controller.abort(), 55000);
  const headers = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers,
  };
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      cache: "no-store",
      ...options,
      headers,
      signal: options.signal || controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok)
      throw createApiError(
        data.detail || `Request failed (${response.status})`,
        response.status
      );
    return data;
  } catch (error) {
    if (error?.name === "AbortError") {
      throw createApiError("Request timed out. Please retry.", 408);
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

export async function loginUser({ email, password }) {
  return apiFetch("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function getCurrentUser(token) {
  const response = await fetch(`${API_BASE_URL}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw createApiError(data.detail || "Failed to fetch user", response.status);
  return data;
}

export async function getCalls(startDate, endDate, params = {}) {
  if (!startDate || !endDate) return [];
  const query = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
    ...params,
  }).toString();
  // Trailing slash avoids a 307 redirect, which breaks CORS preflight for cross-origin requests.
  return apiFetch(`/calls/?${query}`);
}

export function getCallContent(callId) {
  return apiFetch(`/calls/${encodeURIComponent(callId)}/content`);
}

export async function getCallAnalysis(startDate, endDate) {
  if (!startDate || !endDate) return [];
  const query = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
  }).toString();
  return apiFetch(`/calls/analysis?${query}`);
}
export async function updateVoicemailLeft(id, voicemailLeft) {
  if (!id) return null;
  return apiFetch(`/calls/analysis-results/${id}/voicemail-left-status`, {
    method: "PUT",
    body: JSON.stringify({ voicemail_left_if_no_answer: voicemailLeft }),
  });
}

// Alias — some Dashboard.jsx versions import this name instead.
export const updateCallAnalysisVoicemailStatus = updateVoicemailLeft;
export async function getCallAudioUrl(callId) {
  if (!callId) return null;
  const query = new URLSearchParams({ call_id: callId }).toString();
  return apiFetch(`/calls/audio-url?${query}`);
}

export async function updateCallAnalysisFollowUpStatus(id, followUpStatus) {
  if (!id) return null;
  return apiFetch(`/calls/analysis-results/${id}/follow-up-status`, {
    method: "PUT",
    body: JSON.stringify({ follow_up_status: followUpStatus }),
  });
}

// Pending Cases
export async function getPendingCases(startDate, endDate, params = {}) {
  const query = new URLSearchParams({
    ...(startDate ? { start_date: startDate } : {}),
    ...(endDate ? { end_date: endDate } : {}),
    ...params,
  }).toString();
  return apiFetch(`/calls/pending${query ? `?${query}` : ""}`);
}
export async function updatePendingCase(id, payload) {
  if (!id) return null;
  return apiFetch(`/calls/pending/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

// Success Cases
// Mirrors getPendingCases exactly — same query-param shape, same fallback
// to "[]" when called without dates — just points at the new /calls/success
// endpoint, which already returns only eligible (Success - PC / Success -
// GE) calls.
export async function getSuccessCases(startDate, endDate, params = {}) {
  const query = new URLSearchParams({
    ...(startDate ? { start_date: startDate } : {}),
    ...(endDate ? { end_date: endDate } : {}),
    ...params,
  }).toString();
  return apiFetch(`/calls/success${query ? `?${query}` : ""}`);
}

// Success Cases has three separate PATCH endpoints (one per editable
// field) instead of Pending Cases' single combined PATCH, so each gets its
// own thin wrapper — same call/response shape as updatePendingCase.
export async function updateSuccessCaseReviewStatus(id, reviewStatus) {
  if (!id) return null;
  return apiFetch(`/calls/success/${id}/review-status`, {
    method: "PATCH",
    body: JSON.stringify({ review_status: reviewStatus }),
  });
}

export async function updateSuccessCaseReviewComment(id, reviewStatusComment) {
  if (!id) return null;
  return apiFetch(`/calls/success/${id}/review-comment`, {
    method: "PATCH",
    body: JSON.stringify({ review_status_comment: reviewStatusComment }),
  });
}

export async function updateSuccessCaseReviewDate(id, reviewStatusUpdatedDate) {
  if (!id) return null;
  return apiFetch(`/calls/success/${id}/review-date`, {
    method: "PATCH",
    body: JSON.stringify({ review_status_updated_date: reviewStatusUpdatedDate }),
  });
}

// Business Opportunities
// Mirrors getPendingCases / getSuccessCases: same query-param shape (dates +
// arbitrary extra params for pagination/sorting/search), same optional-date
// fallback behavior — just points at the new /business/* endpoints.
export async function getBusinessSummary(startDate, endDate, params = {}) {
  const query = new URLSearchParams({
    ...(startDate ? { start_date: startDate } : {}),
    ...(endDate ? { end_date: endDate } : {}),
    ...params,
  }).toString();
  return apiFetch(`/calls/business/summary${query ? `?${query}` : ""}`);
}

export async function getBusinessCalls(startDate, endDate, params = {}) {
  const query = new URLSearchParams({
    ...(startDate ? { start_date: startDate } : {}),
    ...(endDate ? { end_date: endDate } : {}),
    ...params,
  }).toString();
  return apiFetch(`/calls/business${query ? `?${query}` : ""}`);
}

// Client FAQ
// Same optional-date + arbitrary-params shape as getPendingCases/getBusinessCalls.
export async function getClientFaq(startDate, endDate, params = {}) {
  const query = new URLSearchParams({
    ...(startDate ? { start_date: startDate } : {}),
    ...(endDate ? { end_date: endDate } : {}),
    ...params,
  }).toString();
  return apiFetch(`/calls/client-faq${query ? `?${query}` : ""}`);
}

// User Management (Super User only)
export async function getUsers() {
  return apiFetch("/users");
}
export async function createUser(payload) {
  return apiFetch("/users", { method: "POST", body: JSON.stringify(payload) });
}
export async function updateUser(id, payload) {
  return apiFetch(`/users/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}
export async function deleteUser(id) {
  return apiFetch(`/users/${id}`, { method: "DELETE" });
}
export async function resetUserPassword(id, newPassword) {
  return apiFetch(`/users/${id}/reset-password`, {
    method: "POST",
    body: JSON.stringify({ new_password: newPassword }),
  });
}
export async function updateProfile(payload) {
  return apiFetch("/auth/profile", { method: "PUT", body: JSON.stringify(payload) });
}
export async function changePassword(payload) {
  return apiFetch("/auth/change-password", { method: "POST", body: JSON.stringify(payload) });
}

// Prompts / Analysis Management
export async function getPrompts() {
  return apiFetch("/prompts");
}
export async function createPrompt(payload) {
  return apiFetch("/prompts", { method: "POST", body: JSON.stringify(payload) });
}
export async function updatePrompt(id, payload) {
  return apiFetch(`/prompts/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}
export async function deletePrompt(id) {
  return apiFetch(`/prompts/${id}`, { method: "DELETE" });
}
