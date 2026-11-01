// Export an existing UI snapshot, never regenerate records or identifiers.
export function callsExportValue(call, key, fallback) {
  if (key === "id") return call.isTaskRecord ? call.recordLabel : (call.call_reference || call.id || "");
  if (["clientId", "leadId", "clientPhoneNumber", "agent", "date", "timestamp", "duration", "capturedNumber", "extension"].includes(key)) {
    return call[key] ?? "";
  }
  return fallback(call, key);
}
