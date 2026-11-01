import test from "node:test";
import assert from "node:assert/strict";

import {
  mapImportHeaders,
  parsePastedTable,
  recordsFromImportTable,
} from "./importMapping.js";

const missedCallFields = [
  ["name", "Name"],
  ["contact_number", "Contact Number"],
  ["summary", "Summary"],
];

test("a one-column task import maps and retains its data", () => {
  const table = parsePastedTable("Name\nTaylor Client\nMorgan Client");
  const mapped = mapImportHeaders(table.headers, missedCallFields);

  assert.deepEqual(mapped.mapping, ["name"]);
  assert.deepEqual(recordsFromImportTable({ ...table, ...mapped }), [
    { name: "Taylor Client" },
    { name: "Morgan Client" },
  ]);
});

test("unrecognized headers produce no importable task columns", () => {
  const mapped = mapImportHeaders(["Unrelated Spreadsheet Field"], missedCallFields);
  assert.deepEqual(mapped.mapping, [""]);
});
