import test from "node:test";
import assert from "node:assert/strict";
import { mapSmLeadHeaders, normalizeReceivedDate, parseSmLeadTable, smLeadRecords, smLeadMappingProblems } from "./smLeadsImport.js";

test("all header variants map by name, not position", () => {
  for (const headers of ["Date,Name,Email,Phone Number", "Name,Phone,Email ID,Date Received",
    "Email Address,Client Name,Mobile Number,Date", "DATE_RECEIVED,FULL_NAME,EMAIL_ID,CONTACT_NUMBER"]) {
    const result = mapSmLeadHeaders(headers.split(","));
    assert.deepEqual(result.missing, [], headers);
    assert.deepEqual(result.ambiguities, [], headers);
    assert.equal(Object.keys(result.mapping).length, 4, headers);
  }
  const r = mapSmLeadHeaders("Email Address,Client Name,Mobile Number,Date".split(","));
  assert.deepEqual(r.mapping, { email: 0, name: 1, phone: 2, date_received: 3 });
});

test("Email_ID / email id / EMAIL ID / Email-ID are the same field", () => {
  for (const h of ["Email_ID", "email id", "EMAIL ID", "Email-ID"]) assert.equal(mapSmLeadHeaders([h]).mapping.email, 0);
});

test("extra columns are ignored and stronger match wins", () => {
  const r = mapSmLeadHeaders(["Source", "Email", "Email Address", "Name", "Notes", "Phone", "Date Received", "Date"]);
  assert.equal(r.mapping.email, 1);
  assert.equal(r.mapping.date_received, 6);
  assert.ok(r.ignored.includes("Source") && r.ignored.includes("Notes"));
});

test("equally-strong candidates are reported as ambiguous", () => {
  const r = mapSmLeadHeaders(["Customer Email", "Work Email", "Name", "Phone", "Date"]);
  assert.equal(r.ambiguities[0].key, "email");
  assert.equal(r.mapping.email, undefined);
});

test("missing field is named", () => {
  const table = parseSmLeadTable("Date,Name,Email\n9/25/2026,A,a@x.com", ",");
  const analysis = mapSmLeadHeaders(table.headers);
  assert.deepEqual(smLeadMappingProblems(table, analysis), ["Unable to identify a column for Phone Number. Please check the uploaded file headers."]);
});

test("records, quoted multiline cells and date normalisation", () => {
  const table = parseSmLeadTable('Name,Phone,Email,Date Received,Enquiry\nA,312,a@x.com,09/25/2026,"line1\nline2"', ",");
  const [record] = smLeadRecords(table, mapSmLeadHeaders(table.headers));
  assert.deepEqual(record, { date_received: "2026-09-25", name: "A", email: "a@x.com", phone: "312", enquiry_question: "line1\nline2" });
  assert.equal(normalizeReceivedDate("9/5/2026 10:30"), "2026-09-05");
});
