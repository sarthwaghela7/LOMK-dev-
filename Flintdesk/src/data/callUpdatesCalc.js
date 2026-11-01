// Pure arithmetic for the Call Updates tables. Totals are always derived from
// the displayed cells, so they update the moment a manual value changes.

export const MANUAL_FIELDS = Object.freeze({
  wa_answered: "Calls Answered",
  wa_missed: "Calls Missed-WA",
  initial_emails: "Initial Emails Sent",
});

const num = (value) => Number(value) || 0;

// The Case Status Change values listed under each lifecycle category of the Call Updates table, in the order shown. A call
// can count under two categories (Client Side Delay and Dropped are in both Open Cases and Dropped Cases), so a category's
// figure is the sum of its values and the categories are not added together. Lost values are read as stored ("... & Lost").
export const LIFECYCLE_GROUPS = [
  { key: "open", label: "Open Cases", values: ["Open", "Client Side Delay", "Blocker", "Same Client Context"] },
  { key: "in_transition", label: "In-Transition Cases", values: [
    "Initial email sent (Fee Details shared)", "Initial Email Follow Up", "Fee details shared & FU", "PC link shared", "PC Follow Up"] },
  { key: "lost", label: "Lost Cases", values: null },
  { key: "dropped", label: "Dropped Cases", values: ["Client Side Delay", "Dropped", "Wrong Number Dialed"] },
];

/** { total, lines: [{ label, count }] } for one lifecycle group of one agent-day; a line is shown for every value, zeros included. */
export function groupFigures(group, values = {}) {
  const source = group.values ? values.status_counts || {} : values.lost_counts || {};
  const labels = group.values || Object.keys(source).sort();
  const lines = labels.map((label) => ({ label, count: num(source[label]) }));
  return { total: lines.reduce((sum, line) => sum + line.count, 0), lines };
}

/** Derived per-agent figures for one day's raw values. */
export function agentFigures(values = {}) {
  const totalCalls = num(values.incoming) + num(values.outgoing);
  const totalWhatsApp = num(values.wa_answered) + num(values.wa_missed);
  return {
    totalCalls,
    totalWhatsApp,
    // Bottom "Total" row: calls handled (Category A) plus WhatsApp calls (B).
    callsAndWhatsApp: totalCalls + totalWhatsApp,
    // Case Status outcomes. Initial Emails Sent and VM are counted inside Open
    // Leads (see their row labels), so they are not added again.
    caseStatusTotal: num(values.open_leads) + num(values.successful_ge) + num(values.paid_pc) + num(values.dropped),
  };
}

const sum = (agentIds, pick) => agentIds.reduce((total, id) => total + num(pick(id)), 0);

/**
 * Whole-day totals plus the A+B versus Case Status comparison.
 * `matches` is true when Category A + Category B (the bottom Total) equals the
 * Case Status total across all agents.
 */
export function dayTotals(day, agentIds) {
  const values = (id) => day?.agents?.[id] || {};
  const column = (field) => sum(agentIds, (id) => values(id)[field]);
  const derived = (field) => sum(agentIds, (id) => agentFigures(values(id))[field]);
  const groupColumn = (groupKey) => sum(agentIds, (id) => {
    const group = LIFECYCLE_GROUPS.find((item) => item.key === groupKey);
    return group ? groupFigures(group, values(id)).total : 0;
  });
  const callsAndWhatsApp = derived("callsAndWhatsApp");
  const caseStatusTotal = derived("caseStatusTotal");
  return {
    column,
    groupColumn,
    totalCalls: derived("totalCalls"),
    totalWhatsApp: derived("totalWhatsApp"),
    callsAndWhatsApp,
    caseStatusTotal,
    matches: callsAndWhatsApp === caseStatusTotal,
  };
}

/** Inclusive list of ISO dates between two ISO dates. */
export function datesBetween(start, end) {
  const days = [];
  const cursor = new Date(`${start}T00:00:00Z`);
  const last = new Date(`${end}T00:00:00Z`);
  while (cursor <= last) {
    days.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return days;
}

export function validateRange(start, end, maxDays = 92) {
  if (!start || !end) return "Choose both a From Date and a To Date.";
  if (start > end) return "From Date must be on or before To Date.";
  if (datesBetween(start, end).length > maxDays) return `Choose a range of ${maxDays} days or fewer.`;
  return "";
}

/** "2026-10-01" -> "October 1, 2026" (no timezone shifting). */
export function longDate(iso) {
  const [year, month, day] = String(iso).split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", { year: "numeric", month: "long", day: "numeric" })
    .format(new Date(year, month - 1, day));
}
