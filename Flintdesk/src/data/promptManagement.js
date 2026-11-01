// Prompt Management (Settings): small pure helpers for the panel. The API decides who may change a prompt (Admin only);
// these only decide what the page shows and when Save is offered.

// The server refuses longer prompts (services/fd_prompt_store.py MAX_PROMPT_CHARS); the page stops them first.
export const PROMPT_MAX_CHARS = 20000;

export const PROMPT_FIELDS = [
  { key: "system_prompt", label: "System prompt", rows: 10 },
  { key: "user_prompt_template", label: "User prompt template", rows: 5 },
];

export const accessNotice = (canEdit) => (canEdit
  ? { tone: "edit", label: "Edit access", text: "You can edit prompts. Saving creates a new version and makes it the active one; earlier versions are kept and can be restored." }
  : { tone: "view", label: "View only", text: "You can read the active prompts. Only an Admin can change them." });

export const draftOf = (row) => ({ system_prompt: row?.system_prompt ?? "", user_prompt_template: row?.user_prompt_template ?? "" });

// True when the draft differs from the saved (active) version.
export const isDirty = (draft, row) => PROMPT_FIELDS.some(({ key }) => (draft?.[key] ?? "") !== (row?.[key] ?? ""));

// Save is offered only for a changed draft whose two fields are not blank (the server validates the same).
export const canSave = (draft, row) => isDirty(draft, row)
  && PROMPT_FIELDS.every(({ key }) => String(draft?.[key] ?? "").trim() !== "" && String(draft?.[key] ?? "").length <= PROMPT_MAX_CHARS);

// A short, readable message for a failed request (the server's validation errors can echo the whole prompt back).
export function friendlyPromptError(error) {
  const text = String(error?.message || error || "");
  if (text.includes("string_too_long") || /at most \d+ characters/.test(text)) return `A prompt field is longer than the ${PROMPT_MAX_CHARS.toLocaleString("en-US")}-character limit. Shorten it and try again.`;
  if (!text) return "The request failed. Nothing was changed.";
  return text.length > 240 ? `${text.slice(0, 240)}...` : text;
}

// How many prompts have unsaved edits: what the page reports so leaving it can warn.
export const unsavedPromptCount = (drafts, rows) => rows.filter((row) => drafts[row.prompt_key] && isDirty(drafts[row.prompt_key], row)).length;

export const earlierVersions = (history, promptKey) => (history || [])
  .filter((item) => item.prompt_key === promptKey && !item.is_active)
  .sort((a, b) => Number(b.version) - Number(a.version));

export function formatPromptTimestamp(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
}
