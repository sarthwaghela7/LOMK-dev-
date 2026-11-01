export function isVoicemailCall(call) {
  if (!call || call.isTaskRecord) return false;
  const raw = call.raw || call;
  const aiVoicemail = String(raw.voicemail_left_if_no_answer || "").trim().toLowerCase();
  if (["yes", "true", "1"].includes(aiVoicemail)) return true;
  const outcome = String(raw.fd_a_call_outcome || raw.call_outcome || call.callOutcome || "").trim().toLowerCase();
  return outcome.includes("voicemail");
}

export function getCallKind(call) {
  return isVoicemailCall(call) ? "Voicemail" : "Normal";
}

// A voicemail whose Case Status Change is "Dropped" is still a voicemail
// call (Call Type/filters/getCallKind are unaffected), but the business
// rule is that it must not add to a day's voicemail total on the
// dashboard. This is the single place that rule is expressed; every
// dashboard voicemail-count aggregation should use this instead of
// isVoicemailCall directly.
export function countsAsDashboardVoicemail(call) {
  if (!isVoicemailCall(call)) return false;
  const raw = call.raw || call;
  const status = String(raw.fd_m_case_status_change || "").trim().toLowerCase();
  return status !== "dropped";
}
