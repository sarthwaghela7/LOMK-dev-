import test from "node:test";
import assert from "node:assert/strict";
import {
  LEAD_SOURCE_FILTER_OPTIONS,
  matchesSourceFilter,
  selectedSources,
} from "./leadSourceFilter.js";

test("the dropdown offers the fixed values and Other", () => {
  assert.deepEqual(LEAD_SOURCE_FILTER_OPTIONS, [
    "Websites", "Ads", "google", "metaads", "organic", "ig", "metaaads", "Other",
  ]);
});

test("no selection means All", () => {
  for (const source of ["organic", "chatgpt.com", "", null]) {
    assert.equal(matchesSourceFilter(source, []), true);
    assert.equal(matchesSourceFilter(source, undefined), true);
  }
});

test("several values can be selected at once", () => {
  const chosen = ["google", "metaads"];
  assert.equal(matchesSourceFilter("google", chosen), true);
  assert.equal(matchesSourceFilter("metaads", chosen), true);
  assert.equal(matchesSourceFilter("organic", chosen), false);
  assert.equal(matchesSourceFilter("metaaads", chosen), false);
});

test("matching ignores case and surrounding spaces", () => {
  assert.equal(matchesSourceFilter("Metaads ", ["metaads"]), true);
  assert.equal(matchesSourceFilter(" ORGANIC", ["organic"]), true);
});

test("any other filled-in Source is filed under Other", () => {
  for (const source of ["google ads", "chatgpt.com", "meta ads", "I need a green card", "Immigration consultation"]) {
    assert.equal(matchesSourceFilter(source, ["Other"]), true, source);
    assert.equal(matchesSourceFilter(source, ["google"]), false, source);
  }
});

test("fixed values are never in Other, and a blank Source is not a value", () => {
  for (const source of ["google", "metaads", "organic", "ig", "metaaads", "Websites", "Ads", " Google "]) {
    assert.equal(matchesSourceFilter(source, ["Other"]), false, source);
  }
  assert.equal(matchesSourceFilter("", ["Other"]), false);
  assert.equal(matchesSourceFilter(null, ["Other"]), false);
  assert.equal(matchesSourceFilter("", ["google"]), false);
});

test("Other combines with fixed values", () => {
  const chosen = ["organic", "Other"];
  assert.equal(matchesSourceFilter("organic", chosen), true);
  assert.equal(matchesSourceFilter("chatgpt.com", chosen), true);
  assert.equal(matchesSourceFilter("google", chosen), false);
});

test("a previously saved single value still works", () => {
  assert.deepEqual(selectedSources("metaads"), ["metaads"]);
  assert.deepEqual(selectedSources(""), []);
  assert.equal(matchesSourceFilter("metaads", "metaads"), true);
});
