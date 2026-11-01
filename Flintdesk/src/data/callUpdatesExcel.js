import { CALL_UPDATE_SECTIONS, cellValue } from "./callUpdatesPdf.js";
import { LIFECYCLE_GROUPS, agentFigures, dayTotals, groupFigures } from "./callUpdatesCalc.js";

// The Call Updates tables as one Excel sheet: every day's rows under Date / Category / Metric, one column per agent and the Total,
// with the same figures the screen shows. Numbers are numbers (not text); the date is the ISO day so Excel reads it as a date.
export function callUpdatesExcelSheet({ days, agents }) {
  const agentIds = agents.map((agent) => agent.id);
  const columns = [
    { label: "Date", minWidth: 80 }, { label: "Category", minWidth: 110 }, { label: "Metric", minWidth: 210 },
    ...agents.map((agent) => ({ label: agent.name, minWidth: 80 })), { label: "Total", minWidth: 70 },
  ];
  const rows = [];
  days.forEach((day) => {
    const totals = dayTotals(day, agentIds);
    const valuesOf = (id) => day.agents?.[id] || {};
    CALL_UPDATE_SECTIONS.forEach((section) => section.rows.forEach((row) => {
      rows.push([
        day.date, section.category, row.hint ? `${row.label} ${row.hint}` : row.label,
        ...agentIds.map((id) => cellValue(row, valuesOf(id), agentFigures(valuesOf(id)))),
        row.group ? totals.groupColumn(row.group) : row.derived ? totals[row.derived] : totals.column(row.key),
      ]);
      const group = row.group ? LIFECYCLE_GROUPS.find((item) => item.key === row.group) : null;
      if (!group) return;
      // Each Case Status Change value under its lifecycle row, with each agent's count.
      const labels = [...new Set(agentIds.flatMap((id) => groupFigures(group, valuesOf(id)).lines.map((line) => line.label)))];
      labels.forEach((label) => {
        const counts = agentIds.map((id) => groupFigures(group, valuesOf(id)).lines.find((line) => line.label === label)?.count || 0);
        rows.push([day.date, section.category, `    ${label}`, ...counts, counts.reduce((sum, count) => sum + count, 0)]);
      });
    }));
    rows.push([day.date, "Total (A+B)", "Calls + WhatsApp", ...agentIds.map((id) => agentFigures(valuesOf(id)).callsAndWhatsApp), totals.callsAndWhatsApp]);
  });
  return { name: "Call Updates", columns, rows };
}

export const callUpdatesExcelFilename = (start, end) => `FlintDesk_Call_Updates_${start}_to_${end}.xls`;
