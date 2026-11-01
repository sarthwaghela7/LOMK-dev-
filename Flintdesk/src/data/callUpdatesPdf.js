import { LIFECYCLE_GROUPS, agentFigures, dayTotals, groupFigures, longDate } from "./callUpdatesCalc.js";

// Same row structure as the on-screen tables (see pages/CallUpdates.jsx).
export const CALL_UPDATE_SECTIONS = [
  { category: "A. Vitel Calls", rows: [
    { key: "incoming", label: "Incoming Calls" },
    { key: "outgoing", label: "Outgoing Calls" },
    { key: "missed", label: "Missed Calls", hint: "(Count included in outgoing calls)" },
    { key: "totalCalls", label: "Total Calls", derived: "totalCalls" },
  ] },
  { category: "B. WhatsApp Calls", rows: [
    { key: "wa_answered", label: "Calls Answered", manual: true },
    { key: "wa_missed", label: "Calls Missed-WA", manual: true },
    { key: "totalWhatsApp", label: "Total WhatsApp Calls", derived: "totalWhatsApp" },
  ] },
  { category: "C. Lead Management", rows: [
    // Each lifecycle category shows its figure and, on screen, every Case Status Change value under it with its own count.
    { key: "open_cases", label: "Open Cases", group: "open" },
    { key: "in_transition_cases", label: "In-Transition Cases", group: "in_transition" },
    { key: "lost_cases", label: "Lost Cases", group: "lost" },
    { key: "dropped_cases", label: "Dropped Cases", group: "dropped" },
    { key: "successful_ge", label: "Success GE" },
    { key: "paid_pc", label: "Success PC" },
    { key: "initial_emails", label: "Initial Emails Sent", hint: "(Count included in Open Leads)", manual: true },
    { key: "vm", label: "VM", hint: "(Count included in Open Leads)" },
    // Compared with the bottom Total (Calls + WhatsApp): both cells go green on a match and red on a mismatch.
    { key: "caseStatusTotal", label: "Total Case Status", hint: "(Open Leads + GE + PC + Dropped)", derived: "caseStatusTotal", compare: true },
  ] },
  // Filled from the Follow Ups tab: calls submitted to each agent as Retainer Owner, by the call's date.
  { category: "Follow Ups", rows: [
    { key: "retainer_follow_up", label: "Retainer Follow Up" },
    { key: "retainer_lost", label: "Retainer Lost" },
    { key: "retainer_success", label: "Success Retainer" },
  ] },
];

/**
 * Initial Emails Sent notes for one day as table rows: one row per note line,
 * grouped by agent. [{ agent, count, lines: [..] }]
 */
export function initialEmailNoteRows(day, agents) {
  return agents.flatMap((agent) => {
    const values = day.agents?.[agent.id] || {};
    const lines = String(values.initial_emails_note || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    return lines.length ? [{ agent: agent.name, count: Number(values.initial_emails) || 0, lines }] : [];
  });
}

export function cellValue(row, values, figures) {
  if (row.group) return groupFigures(LIFECYCLE_GROUPS.find((group) => group.key === row.group), values).total;
  return row.derived ? figures[row.derived] : Number(values?.[row.key]) || 0;
}

const GREEN = [220, 252, 231];
const RED = [254, 226, 226];
const HEADER = [226, 238, 250];
const CATEGORY = [243, 247, 252];

export async function exportCallUpdatesPdf({ days, agents, start, end }) {
  const { jsPDF } = await import("jspdf");
  const pdf = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4" });
  const pageWidth = pdf.internal.pageSize.getWidth();
  const pageHeight = pdf.internal.pageSize.getHeight();
  const margin = 10;
  const categoryWidth = 32;
  const metricWidth = 58;
  const totalWidth = 18;
  const agentWidth = Math.max(14, (pageWidth - margin * 2 - categoryWidth - metricWidth - totalWidth) / Math.max(agents.length, 1));
  const rowHeight = 6.4;
  const agentIds = agents.map((agent) => agent.id);

  days.forEach((day, index) => {
    if (index) pdf.addPage();
    const totals = dayTotals(day, agentIds);
    let y = margin + 4;
    pdf.setFont("helvetica", "bold").setFontSize(13).setTextColor(20);
    pdf.text(`Call Updates – ${longDate(day.date)}`, margin, y);
    y += 6;
    const compareFill = totals.matches ? GREEN : RED;

    const cell = (text, x, width, { bold = false, fill = null, align = "center", height = rowHeight, size = 8, maxLines = 2, fromTop = false } = {}) => {
      if (fill) pdf.setFillColor(...fill).rect(x, y, width, height, "F");
      pdf.setDrawColor(150).rect(x, y, width, height);
      pdf.setFont("helvetica", bold ? "bold" : "normal").setFontSize(size).setTextColor(20);
      const label = String(text);
      const lines = pdf.splitTextToSize(label, width - 2).slice(0, maxLines);
      const textY = fromTop ? y + 3.4 : y + height / 2 + (lines.length > 1 ? -1 : 1);
      pdf.text(lines, align === "left" ? x + 1.5 : x + width / 2, textY, { align: align === "left" ? "left" : "center" });
    };
    // A lifecycle row prints its figure and, under it, every Case Status Change value with its count (as the table on screen does).
    const VALUE_SIZE = 5.6;
    const VALUE_STEP = VALUE_SIZE * 0.3528 * 1.2;
    const groupText = (row, values) => {
      const group = LIFECYCLE_GROUPS.find((item) => item.key === row.group);
      const { total, lines } = groupFigures(group, values);
      pdf.setFont("helvetica", "normal").setFontSize(VALUE_SIZE);
      const wrapped = lines.flatMap((line) => pdf.splitTextToSize(`${line.label}: ${line.count}`, agentWidth - 3));
      return { total, wrapped };
    };

    let x = margin;
    const drawHeader = () => {
      let hx = margin;
      cell("Category", hx, categoryWidth, { bold: true, fill: HEADER }); hx += categoryWidth;
      cell("Metric", hx, metricWidth, { bold: true, fill: HEADER, align: "left" }); hx += metricWidth;
      agents.forEach((agent) => { cell(agent.name, hx, agentWidth, { bold: true, fill: HEADER, size: 7.5 }); hx += agentWidth; });
      cell("Total", hx, totalWidth, { bold: true, fill: HEADER });
      y += rowHeight;
    };
    // A new page repeats the title and the column header, so every page of a long day can be read on its own.
    const newPage = () => {
      pdf.addPage(); y = margin + 4;
      pdf.setFont("helvetica", "bold").setFontSize(11).setTextColor(20);
      pdf.text(`Call Updates – ${longDate(day.date)} (continued)`, margin, y);
      y += 5;
      drawHeader();
    };
    const ensureRoom = (needed) => { if (y + needed > pageHeight - margin) newPage(); };
    drawHeader();

    CALL_UPDATE_SECTIONS.forEach((section) => {
      let startY = y;
      // The category label box covers the rows of ITS PAGE only, so a section that continues on the next page is labelled again there.
      const closeSegment = () => {
        const spanHeight = y - startY;
        if (spanHeight <= 0) return;
        pdf.setFillColor(...CATEGORY).rect(margin, startY, categoryWidth, spanHeight, "F");
        pdf.setDrawColor(150).rect(margin, startY, categoryWidth, spanHeight);
        pdf.setFont("helvetica", "bold").setFontSize(8).setTextColor(20);
        pdf.text(pdf.splitTextToSize(section.category, categoryWidth - 3), margin + 1.5, startY + spanHeight / 2 - 1);
      };
      section.rows.forEach((row) => {
        const groupCells = row.group ? agents.map((agent) => groupText(row, day.agents?.[agent.id] || {})) : null;
        const height = groupCells ? Math.max(rowHeight, 8.6 + Math.max(...groupCells.map((item) => item.wrapped.length)) * VALUE_STEP) : rowHeight;
        if (y + height > pageHeight - margin) { closeSegment(); newPage(); startY = y; }       // keeps a long row whole instead of cutting it off
        x = margin + categoryWidth;
        cell(row.hint ? `${row.label} ${row.hint}` : row.label, x, metricWidth, { align: "left", bold: Boolean(row.derived), size: row.hint ? 7 : 8, height });
        x += metricWidth;
        agents.forEach((agent, agentIndex) => {
          const values = day.agents?.[agent.id] || {};
          if (groupCells) {
            const { total, wrapped } = groupCells[agentIndex];
            cell(total, x, agentWidth, { bold: true, height, fromTop: true });
            pdf.setFont("helvetica", "normal").setFontSize(VALUE_SIZE).setTextColor(70);
            wrapped.forEach((line, lineIndex) => pdf.text(line, x + 1.5, y + 7.2 + lineIndex * VALUE_STEP));
          } else {
            cell(cellValue(row, values, agentFigures(values)), x, agentWidth, { bold: Boolean(row.derived), height });
          }
          x += agentWidth;
        });
        const total = row.group ? totals.groupColumn(row.group) : row.derived ? totals[row.derived] : totals.column(row.key);
        cell(total, x, totalWidth, { bold: true, fill: row.compare ? compareFill : CATEGORY, height });
        y += height;
      });
      closeSegment();
    });

    // Bottom Total row: Total Calls + Total WhatsApp Calls.
    ensureRoom(rowHeight + 12);                       // the total row and its match line stay together
    x = margin;
    cell("Total (A+B)", x, categoryWidth + metricWidth, { bold: true, fill: CATEGORY, align: "left" });
    x += categoryWidth + metricWidth;
    agents.forEach((agent) => { cell(agentFigures(day.agents?.[agent.id]).callsAndWhatsApp, x, agentWidth, { bold: true, fill: CATEGORY }); x += agentWidth; });
    cell(totals.callsAndWhatsApp, x, totalWidth, { bold: true, fill: compareFill });
    y += rowHeight + 5;

    pdf.setFont("helvetica", "normal").setFontSize(8).setTextColor(60);
    pdf.text(
      `Calls + WhatsApp = ${totals.callsAndWhatsApp}   |   Case Status (Open Leads + GE + PC + Dropped) = ${totals.caseStatusTotal}   |   ${totals.matches ? "Match" : "Mismatch"}`,
      margin, y,
    );
    y += 5;
    const noteRows = initialEmailNoteRows(day, agents);
    if (noteRows.length) {
      const labelWidth = 48;
      const agentColumn = 30;
      const countColumn = 16;
      const textWidth = pageWidth - margin * 2 - labelWidth - agentColumn - countColumn;
      const cellHeight = (lineCount) => Math.max(rowHeight, lineCount * 4 + 2.4);
      const rows = noteRows.flatMap((item) => item.lines.map((line, index) => ({
        agent: index === 0 ? item.agent : "", count: index === 0 ? item.count : "",
        wrapped: pdf.setFont("helvetica", "normal").setFontSize(8).splitTextToSize(line, textWidth - 3),
      })));
      const total = rows.reduce((sum, row) => sum + cellHeight(row.wrapped.length), 0);
      if (y + total > pageHeight - margin) y = Math.max(margin, pageHeight - margin - total);
      const top = y;
      pdf.setFillColor(...CATEGORY).rect(margin, top, labelWidth, total, "F");
      pdf.setDrawColor(150).rect(margin, top, labelWidth, total);
      pdf.setFont("helvetica", "bold").setFontSize(8).setTextColor(20);
      pdf.text(pdf.splitTextToSize("Initial Email Sent Categories", labelWidth - 3), margin + 1.5, top + 4.5);
      rows.forEach((row) => {
        const height = cellHeight(row.wrapped.length);
        let cx = margin + labelWidth;
        pdf.setDrawColor(150);
        pdf.rect(cx, y, agentColumn, height); pdf.rect(cx + agentColumn, y, countColumn, height); pdf.rect(cx + agentColumn + countColumn, y, textWidth, height);
        pdf.setFont("helvetica", "bold").setFontSize(8).setTextColor(20);
        pdf.text(String(row.agent), cx + 1.5, y + 4.4);
        pdf.text(String(row.count), cx + agentColumn + countColumn / 2, y + 4.4, { align: "center" });
        pdf.setFont("helvetica", "normal").text(row.wrapped, cx + agentColumn + countColumn + 1.5, y + 4.4);
        y += height;
      });
    }
  });

  pdf.save(`FlintDesk_Call_Updates_${start}_to_${end}.pdf`);
}
