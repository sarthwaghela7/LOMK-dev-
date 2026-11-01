import test from "node:test";
import assert from "node:assert/strict";

import { PROMPT_MAX_CHARS, accessNotice, canSave, draftOf, earlierVersions, formatPromptTimestamp, friendlyPromptError, isDirty, unsavedPromptCount } from "./promptManagement.js";

const row = { prompt_key: "a", system_prompt: "S", user_prompt_template: "U" };

test("the access banner says whether the user can edit", () => {
  assert.equal(accessNotice(true).tone, "edit");
  assert.equal(accessNotice(false).tone, "view");
  assert.match(accessNotice(false).text, /Only an Admin/);
});

test("a draft is dirty only when it differs from the saved version", () => {
  assert.equal(isDirty(draftOf(row), row), false);
  assert.equal(isDirty({ ...draftOf(row), system_prompt: "S2" }, row), true);
  assert.equal(isDirty({ ...draftOf(row), user_prompt_template: "" }, row), true);
});

test("Save is offered only for a changed draft with both fields filled", () => {
  assert.equal(canSave(draftOf(row), row), false);
  assert.equal(canSave({ system_prompt: "S2", user_prompt_template: "U" }, row), true);
  assert.equal(canSave({ system_prompt: "   ", user_prompt_template: "U" }, row), false);
  assert.equal(canSave({ system_prompt: "S2", user_prompt_template: "" }, row), false);
});

test("unsaved prompts are counted so leaving the page can warn", () => {
  const rows = [row, { ...row, prompt_key: "b" }];
  assert.equal(unsavedPromptCount({}, rows), 0);
  assert.equal(unsavedPromptCount({ a: draftOf(row), b: { system_prompt: "x", user_prompt_template: "U" } }, rows), 1);
});

test("earlier versions are the inactive ones of that prompt, newest first", () => {
  const history = [{ prompt_key: "a", version: 1, is_active: false }, { prompt_key: "a", version: 3, is_active: true }, { prompt_key: "a", version: 2, is_active: false }, { prompt_key: "b", version: 9, is_active: false }];
  assert.deepEqual(earlierVersions(history, "a").map((item) => item.version), [2, 1]);
});

test("timestamps are shown in Central Time and a bad value is blank", () => {
  assert.match(formatPromptTimestamp("2026-10-20T09:09:00Z"), /Oct 20, 2026/);
  assert.equal(formatPromptTimestamp("not a date"), "");
  assert.equal(formatPromptTimestamp(null), "");
});

test("a prompt over the server's limit cannot be saved, and a server error is shown briefly", () => {
  assert.equal(canSave({ system_prompt: "x".repeat(PROMPT_MAX_CHARS), user_prompt_template: "U" }, row), true);
  assert.equal(canSave({ system_prompt: "x".repeat(PROMPT_MAX_CHARS + 1), user_prompt_template: "U" }, row), false);
  assert.match(friendlyPromptError(new Error('[{"type":"string_too_long","input":"' + "x".repeat(5000) + '"}]')), /20,000-character limit/);
  assert.ok(friendlyPromptError(new Error("y".repeat(900))).length < 260);
  assert.equal(friendlyPromptError(new Error("Admin access required")), "Admin access required");
  assert.match(friendlyPromptError(null), /Nothing was changed/);
});
