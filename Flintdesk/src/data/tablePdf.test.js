import test from "node:test";
import assert from "node:assert/strict";

import { columnParts, columnWidths } from "./tablePdf.js";

test("column widths stay within the readable range", () => {
  const widths = columnWidths(["A", "A very long heading indeed"], [["x", "y".repeat(500)]]);
  widths.forEach((width) => assert.ok(width >= 18 && width <= 62));
});

test("a wide table is split into parts that each repeat the first column and fit the page", () => {
  const widths = Array(12).fill(40);
  const parts = columnParts(widths, 277, 1);
  assert.ok(parts.length > 1);
  parts.forEach((part) => {
    assert.equal(part[0], 0);
    assert.ok(part.reduce((sum, index) => sum + widths[index], 0) <= 277);
  });
  assert.deepEqual([...new Set(parts.flat())].sort((a, b) => a - b), widths.map((_, i) => i));
});

test("a narrow table stays one part", () => {
  assert.equal(columnParts([30, 30, 30], 277, 1).length, 1);
});

test("a heading is never wider than its column in a single word", () => {
  const [width] = columnWidths(["Dependency"], [["x"]]);
  assert.ok(width >= "Dependency".length * 2.1);
});
