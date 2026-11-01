// Single source of truth for closed-list dropdown values shared between the
// Calls and Leads pages, so Case Status stays identical everywhere it is
// edited instead of drifting into per-page copies.
//
// These are the agent-selectable statuses only. The case LIFECYCLE stage (Open / In-Transition / Lost / Dropped /
// Success) is not a dropdown value: the backend derives it (AutoFlint_Backend/services/case_lifecycle.py), and a Lost
// call shows its last In-Transition status with " & Lost" appended.
export const CASE_STATUS_OPTIONS = [
  "Open",
  "Client Side Delay",
  "Blocker",
  "Dropped",
  "Same Client Context",
  "Wrong Number Dialed",
  "Initial email sent (Fee Details shared)",
  "Initial Email Follow Up",
  "Fee details shared & FU",
  "PC link shared",
  "PC Follow Up",
  "Success PC",
  "Success GE",
];

export const LEAD_ACTION_STATUS_OPTIONS = ["Open", "Completed", "No Action Required"];

// Spellings stored before the dropdown change. The backend rewrites them to the new wording; until then a record may
// still carry one, so any comparison that decides "closed" must understand both.
export const LEGACY_CASE_STATUS_ALIASES = {
  "success - pc": "Success PC",
  "success - ge": "Success GE",
  "initial email followup": "Initial Email Follow Up",
  "initial email sent.": "Initial email sent (Fee Details shared)",
  "pc shared and lost": "PC link shared & Lost",
};

// Comparison key: trimmed, single-spaced, lower-case; legacy spellings are folded into the new wording.
export function caseStatusKey(value) {
  const key = String(value || "").trim().replace(/\s+/g, " ").toLowerCase();
  return (LEGACY_CASE_STATUS_ALIASES[key] || key).toLowerCase();
}

export const isLostStatus = (value) => caseStatusKey(value).endsWith("& lost");
