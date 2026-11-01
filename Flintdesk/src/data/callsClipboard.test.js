import test from "node:test";
import assert from "node:assert/strict";

import {
  buildCopyTable, sanitizeCell, toClipboardHtml, toTabSeparated, writeTableToClipboard,
} from "./callsClipboard.js";
import { formatDisplayDateTime } from "./displayDate.js";

const columns = [
  { key: "id", label: "Call ID" },
  { key: "clientPhoneNumber", label: "Client Phone Number" },
  { key: "date", label: "Date" },
  { key: "duration", label: "Duration" },
  { key: "fd_m_self_action_item", label: "Self Action Item" },
];
const dateAsShown = { date: (value) => formatDisplayDateTime(value, "") };

test("header row comes first, then one row per call, split by real tabs and newlines", () => {
  const table = buildCopyTable(columns, [
    ["AF#12369", "+19133886745", "2026-10-09", "3:01", "Call back"],
    ["AF#12365", "2243028188", "2026-10-08", "0:23", "Send forms"],
  ], dateAsShown);
  const lines = toTabSeparated(table).split("\n");
  assert.equal(lines.length, 3);
  assert.equal(lines[0], "Call ID\tClient Phone Number\tDate\tDuration\tSelf Action Item");
  assert.equal(lines[1], "AF#12369\t+19133886745\t10/09/2026\t3:01\tCall back");
  assert.equal(lines[2], "AF#12365\t2243028188\t10/08/2026\t0:23\tSend forms");
  assert.ok(!toTabSeparated(table).includes("|"), "no literal pipes");
  assert.ok(lines.every((line) => line.split("\t").length === columns.length), "every row lines up with the header");
});

test("a header-only copy still works when there are no rows", () => {
  assert.equal(toTabSeparated(buildCopyTable(columns, [])), "Call ID\tClient Phone Number\tDate\tDuration\tSelf Action Item");
});

test("empty and missing values keep their column (nothing shifts left)", () => {
  const table = buildCopyTable(columns, [["AF#1", "", null, undefined, "x"], ["AF#2"]]);
  const rows = toTabSeparated(table).split("\n").slice(1).map((line) => line.split("\t"));
  assert.deepEqual(rows[0], ["AF#1", "", "", "", "x"]);
  assert.deepEqual(rows[1], ["AF#2", "", "", "", ""]);
});

test("tabs, line breaks and separators inside a value cannot corrupt the table", () => {
  const nasty = "line one\nline two\r\n\tindented\u2028end";
  const table = buildCopyTable(columns, [["AF#1", "1", "2026-10-09", "0:01", nasty]]);
  const text = toTabSeparated(table);
  assert.equal(text.split("\n").length, 2);
  assert.equal(text.split("\n")[1].split("\t").length, columns.length);
  assert.equal(sanitizeCell(nasty), "line one line two indented end");
});

test("phone numbers and identifiers are copied exactly as displayed", () => {
  const table = buildCopyTable(columns, [["AF#12369", "011917338878812", "2026-10-09", "3:01", ""]]);
  assert.equal(table.rows[0][1], "011917338878812");
  assert.equal(table.rows[0][0], "AF#12369");
});

test("dates use the displayed MM/DD/YYYY form, with no timezone shift", () => {
  const previous = process.env.TZ;
  for (const zone of ["UTC", "America/Chicago", "Pacific/Auckland"]) {
    process.env.TZ = zone;
    assert.equal(buildCopyTable(columns, [["a", "b", "2026-10-09", "c", "d"]], dateAsShown).rows[0][2], "10/09/2026");
  }
  process.env.TZ = previous;
  assert.equal(buildCopyTable(columns, [["a", "b", "", "c", "d"]], dateAsShown).rows[0][2], "");
});

test("text that would run as a spreadsheet formula is marked as text", () => {
  assert.equal(sanitizeCell("=HYPERLINK(\"http://x\")"), "'=HYPERLINK(\"http://x\")");
  assert.equal(sanitizeCell("@SUM(A1)"), "'@SUM(A1)");
  assert.equal(sanitizeCell("+19133886745"), "+19133886745"); // phone numbers are left alone
  assert.equal(sanitizeCell("Follow up = tomorrow"), "Follow up = tomorrow");
});

test("the HTML flavor marks phone numbers, IDs and times as text and escapes markup", () => {
  const table = buildCopyTable(columns, [["AF#1", "+19133886745", "2026-10-09", "3:01", "<b>x</b> & \"y\""]], dateAsShown);
  const html = toClipboardHtml(table);
  assert.match(html, /<td style="mso-number-format:'\\@'" data-sheets-value="[^"]*">\+19133886745<\/td>/);
  assert.match(html, /<td style="mso-number-format:'\\@'"[^>]*>3:01<\/td>/);
  assert.ok(html.includes("<td>10/09/2026</td>"), "dates stay normal cells");
  assert.ok(html.includes("&lt;b&gt;x&lt;/b&gt; &amp; &quot;y&quot;"), "markup in values is escaped");
  assert.ok(html.startsWith("<table>") && html.endsWith("</table>"));
});

function fakeClipboard({ write = "ok", writeText = "ok" } = {}) {
  const seen = { write: null, writeText: null };
  const clipboard = {
    write: write === "missing" ? undefined : async (items) => { if (write === "fail") throw new Error("denied"); seen.write = items; },
    writeText: writeText === "missing" ? undefined : async (text) => { if (writeText === "fail") throw new Error("denied"); seen.writeText = text; },
  };
  return { nav: { clipboard }, seen };
}
class FakeItem { constructor(parts) { this.parts = parts; } }
const small = buildCopyTable(columns, [["AF#1", "1", "2026-10-09", "0:01", "x"]], dateAsShown);

test("clipboard write sends both the tab-separated text and the HTML table", async () => {
  const { nav, seen } = fakeClipboard();
  assert.equal(await writeTableToClipboard(small, nav, FakeItem), "rich");
  const item = seen.write[0];
  assert.deepEqual(Object.keys(item.parts), ["text/plain", "text/html"]);
  assert.equal(await item.parts["text/plain"].text(), toTabSeparated(small));
  assert.match(await item.parts["text/html"].text(), /^<table>/);
});

test("if the rich write is refused, plain text is tried", async () => {
  const { nav, seen } = fakeClipboard({ write: "fail" });
  assert.equal(await writeTableToClipboard(small, nav, FakeItem), "plain");
  assert.equal(seen.writeText, toTabSeparated(small));
});

test("without the rich API, plain text is used", async () => {
  const { nav, seen } = fakeClipboard({ write: "missing" });
  assert.equal(await writeTableToClipboard(small, nav, undefined), "plain");
  assert.equal(seen.writeText, toTabSeparated(small));
});

test("a permission failure rejects, so the page never reports a false success", async () => {
  await assert.rejects(writeTableToClipboard(small, fakeClipboard({ write: "fail", writeText: "fail" }).nav, FakeItem), /denied/);
  await assert.rejects(writeTableToClipboard(small, { clipboard: undefined }, FakeItem), /not available/);
  await assert.rejects(writeTableToClipboard(small, fakeClipboard({ write: "missing", writeText: "missing" }).nav, undefined), /not available/);
});
