import test from "node:test";
import assert from "node:assert/strict";
import { callsExportValue } from "./callsExport.js";

test("export preserves displayed identifiers rather than raw record IDs", () => {
  const call = { id: "display-fallback", call_reference: "AF#147", callId: "call-uuid", clientId: "000123", raw: { id: "analysis-uuid", clientId: "different-id" } };
  const before = JSON.stringify(call);
  const fallback = () => { throw new Error("Identifiers must not use raw values"); };
  assert.equal(callsExportValue(call, "id", fallback), "AF#147");
  assert.equal(callsExportValue(call, "clientId", fallback), "000123");
  assert.equal(JSON.stringify(call), before);
});

test("export preserves missing IDs and task labels without generating IDs", () => {
  assert.equal(callsExportValue({}, "id"), "");
  assert.equal(callsExportValue({}, "clientId"), "");
  assert.equal(callsExportValue({ isTaskRecord: true, recordLabel: "Missed Call", id: "task-uuid" }, "id"), "Missed Call");
});

test("stage values use the existing cell formatter", () => {
  const call = { raw: { fd_m_blocker_comment: "Saved comment" } };
  assert.equal(callsExportValue(call, "fd_m_blocker_comment", (row, key) => row.raw[key]), "Saved comment");
});

test("export includes the Client Phone Number the table shows, unchanged", () => {
  const fallback = () => { throw new Error("Client phone must come from the displayed row"); };
  assert.equal(callsExportValue({ clientPhoneNumber: "+13094440696" }, "clientPhoneNumber", fallback), "+13094440696");
  assert.equal(callsExportValue({ clientPhoneNumber: "011917338878812" }, "clientPhoneNumber", fallback), "011917338878812");
  assert.equal(callsExportValue({}, "clientPhoneNumber", fallback), "");
});
