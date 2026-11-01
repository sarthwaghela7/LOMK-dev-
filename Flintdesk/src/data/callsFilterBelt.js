// The Calls page's consolidated filter belt: which dropdowns exist, their values, how the chosen values become the
// server's filters, and how many are active. Pure functions (no React, no network), tested in callsFilterBelt.test.js.
import { LIFECYCLE_STAGES, validLifecycleStage } from "./caseLifecycle.js";

export const LIFECYCLE_OPTIONS = LIFECYCLE_STAGES.map(({ key, label }) => ({ value: key, label }));

// Open Cases: the three rows that sit under the Dashboard's Open Call Aging cards. "agent" and "other_agent" are the AI's
// saved dependency answers for the calls of the agent chosen in this belt.
export const OPEN_CASE_SAME_CONTEXT = "same_context";
export const OPEN_CASE_AGENT = "agent";
export const OPEN_CASE_OTHER_AGENT = "other_agent";
const OPEN_CASE_VALUES = new Set([OPEN_CASE_SAME_CONTEXT, OPEN_CASE_AGENT, OPEN_CASE_OTHER_AGENT]);
export const SAME_CLIENT_CONTEXT_STATUS = "Same Client Context";

export const openCaseOptions = (agentName) => {
  const hasAgent = Boolean(agentName) && agentName !== "all";
  return [
    { value: OPEN_CASE_SAME_CONTEXT, label: "Same Client Context" },
    { value: OPEN_CASE_AGENT, label: hasAgent ? `${agentName} Dependency` : "Agent Dependency (choose an agent first)", disabled: !hasAgent },
    { value: OPEN_CASE_OTHER_AGENT, label: "Other Agents Dependency", disabled: !hasAgent },
  ];
};

// Same forms and wording as the Priority Cases logic; H4EAD is stored without the hyphen.
export const PRIORITY_OPTIONS = [
  { value: "RFE", label: "RFE" },
  { value: "I-130", label: "I-130" },
  { value: "I-485", label: "I-485" },
  { value: "H4", label: "H4" },
  { value: "H4EAD", label: "H4-EAD" },
];

export const ACTION_STATUS_OPTIONS = [{ value: "Completed", label: "Completed" }, { value: "Pending", label: "Pending" }];

// Log Updates is the green Completed button of each call: Pending = not green, Completed = green. The server's
// review_status values are "pending" and "completed"; "all" means no filter.
export const LOG_UPDATE_OPTIONS = [{ value: "pending", label: "Pending" }, { value: "completed", label: "Completed" }];

export const validOpenCase = (value) => (OPEN_CASE_VALUES.has(value) ? value : "");
export const validPriorityForm = (value) => (PRIORITY_OPTIONS.some((option) => option.value === value) ? value : "");
export const validActionStatus = (value) => (ACTION_STATUS_OPTIONS.some((option) => option.value === value) ? value : "");
export const validLogUpdate = (value) => (LOG_UPDATE_OPTIONS.some((option) => option.value === value) ? value : "all");

// The selected agent is needed by both dependency choices; without one they cannot apply and are dropped.
export const effectiveOpenCase = (openCase, agent) => {
  const value = validOpenCase(openCase);
  return (value === OPEN_CASE_AGENT || value === OPEN_CASE_OTHER_AGENT) && (!agent || agent === "all") ? "" : value;
};

// The server filters for the belt's Open Cases choice. "Same Client Context" is the existing Case Status filter;
// the two dependencies use the saved AI answer, all ages.
export function openCaseRequest(openCase, agent, caseStatus = "") {
  const value = effectiveOpenCase(openCase, agent);
  if (value === OPEN_CASE_SAME_CONTEXT) return { caseStatus: SAME_CLIENT_CONTEXT_STATUS, agingDependency: "" };
  if (value === OPEN_CASE_AGENT || value === OPEN_CASE_OTHER_AGENT) return { caseStatus, agingDependency: value };
  return { caseStatus, agingDependency: "" };
}

// How many of the belt's filters are applied, for the badge and the "filters on" indication.
export function activeBeltFilterCount({ lifecycleStage, openCase, priorityForm, actionStatus, logUpdate, followUpDate }) {
  return [validLifecycleStage(lifecycleStage), validOpenCase(openCase), validPriorityForm(priorityForm), validActionStatus(actionStatus),
    validLogUpdate(logUpdate) === "all" ? "" : logUpdate, String(followUpDate || "")].filter(Boolean).length;
}

export const sameBeltSelection = (a, b) =>
  ["lifecycleStage", "openCase", "priorityForm", "actionStatus", "logUpdate", "followUpDate"].every((key) => String(a?.[key] || "") === String(b?.[key] || ""));

// Every server filter the belt and the Case Status data filter produce, in the names the API helpers take. The Open Cases
// choice may set the Case Status ("Same Client Context"); otherwise the Case Status filter stays as it is.
export function beltServerFilters({ openCase, agent, caseStatus = "", actionStatus = "", priorityForm = "" }) {
  const open = openCaseRequest(openCase, agent, caseStatus);
  return { caseStatus: open.caseStatus, agingDependency: open.agingDependency, actionStatus: validActionStatus(actionStatus), priorityForm: validPriorityForm(priorityForm) };
}
