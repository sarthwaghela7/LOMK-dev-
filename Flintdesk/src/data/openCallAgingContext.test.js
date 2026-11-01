import test from "node:test";
import assert from "node:assert/strict";

import { AGING_PLACEHOLDER, agingCardValue, agingContextNote, agingContextRows, formatAiSince, resolveAgingCards } from "./openCallAgingContext.js";

const context = {
  agent: "Google", aiEnabled: true, unanalysed: 0,
  sameClientContext: { 3: 4, 5: 3, 10: 2, 15: 1 },
  agentDependency: { 3: 5, 5: 4, 10: 3, 15: 2 },
  otherAgentDependency: { 3: 6, 5: 5, 10: 4, 15: 3 },
};

test("with an agent selected each card shows the three values for its own age bucket", () => {
  assert.deepEqual(agingContextRows(context, "Google", 3).map((row) => [row.label, row.value]),
    [["Same client context", 4], ["Google dependency", 5], ["Other agents dependency", 6]]);
  assert.deepEqual(agingContextRows(context, "Google", 15).map((row) => row.value), [1, 2, 3]);
});

test("nothing is shown for All Agents, without context, or for an old payload", () => {
  assert.equal(agingContextRows(context, "all", 3), null);
  assert.equal(agingContextRows(context, "", 3), null);
  assert.equal(agingContextRows(undefined, "Google", 3), null);
  assert.equal(agingContextRows(null, "Google", 3), null);
});

test("missing or malformed buckets read as 0, never NaN", () => {
  const rows = agingContextRows({ aiEnabled: true, sameClientContext: { 3: "x" } }, "Ravi", 3);
  assert.deepEqual(rows.map((row) => row.value), [0, 0, 0]);
  assert.equal(rows[1].label, "Ravi dependency");
});

test("the note explains an incomplete AI read and is silent otherwise", () => {
  assert.equal(agingContextNote(context, "Google"), "");
  assert.match(agingContextNote({ ...context, unanalysed: 1 }, "Google"), /1 open call is still waiting/);
  assert.match(agingContextNote({ ...context, unanalysed: 7 }, "Google"), /7 open calls are still waiting/);
  assert.equal(agingContextNote(context, "all"), "");
});

test("with the AI read switched off the cards show only Same client context and there is no AI note at all", () => {
  const off = { ...context, aiEnabled: false, unanalysed: 50, aiSince: "2026-10-14" };
  assert.deepEqual(agingContextRows(off, "Google", 3).map((row) => [row.label, row.value]), [["Same client context", 4]]);
  assert.equal(agingContextNote(off, "Google"), "");
  const missing = { sameClientContext: { 3: 2 }, agent: "Google" };                   // an older payload without the flag behaves the same
  assert.deepEqual(agingContextRows(missing, "Google", 3).map((row) => row.key), ["sameClientContext"]);
  assert.equal(agingContextNote(missing, "Google"), "");
});

const KEY = '["2026-10-01","2026-10-31","all","",""]';
const ages = { 3: 5, 5: 4, 10: 2, 15: 0 };

test("until the aging answer arrives the cards show a placeholder (the rest of the Dashboard is already visible)", () => {
  const cards = resolveAgingCards(null, KEY, { 3: 9, 5: 9, 10: 9, 15: 9 });
  assert.equal(cards.state, "loading");
  assert.deepEqual([3, 5, 10, 15].map((d) => agingCardValue(cards, d)), [AGING_PLACEHOLDER, AGING_PLACEHOLDER, AGING_PLACEHOLDER, AGING_PLACEHOLDER]);
  assert.equal(cards.context, null);
});

test("an answer for different filters is never shown for the current ones", () => {
  const cards = resolveAgingCards({ key: "other", data: { openCallAges: ages, openCallAgeContext: {} } }, KEY, null);
  assert.equal(cards.state, "loading");
});

test("the answer for the current filters fills the cards and the three lines, and survives a quiet refresh", () => {
  const aging = { key: KEY, data: { openCallAges: ages, openCallAgeContext: context } };
  const cards = resolveAgingCards(aging, KEY, null);
  assert.equal(cards.state, "ready");
  assert.deepEqual([3, 5, 10, 15].map((d) => agingCardValue(cards, d)), [5, 4, 2, 0]);
  assert.equal(cards.context, context);
  assert.equal(resolveAgingCards(aging, KEY, null).state, "ready");                  // same filters again: nothing flickers back to loading
});

test("if the aging request fails the cards fall back to the summary's figure instead of staying empty", () => {
  const cards = resolveAgingCards({ key: KEY, data: null }, KEY, { 3: 7, 5: 6, 10: 3, 15: 1 });
  assert.equal(cards.state, "fallback");
  assert.deepEqual([3, 5, 10, 15].map((d) => agingCardValue(cards, d)), [7, 6, 3, 1]);
  assert.equal(cards.context, null);
});

test("the note tells the agent from which date the AI reads", () => {
  assert.equal(formatAiSince("2026-10-14"), "14 Oct 2026");
  assert.equal(formatAiSince("garbage"), "");
  assert.equal(agingContextNote({ ...context, aiSince: "2026-10-14" }, "Google"), "AI dependency figures cover calls from 14 Oct 2026 onward.");
  assert.match(agingContextNote({ ...context, aiSince: "2026-10-14", unanalysed: 3 }, "Google"), /cover calls from 14 Oct 2026 onward\. 3 open calls are still waiting/);
  assert.equal(agingContextNote({ ...context, aiSince: "2026-10-14" }, "all"), "");
});
