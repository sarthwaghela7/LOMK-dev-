import { formatGeneratedAt, formatShortDate, groupQuestionsByCategory, periodRangeText, questionsMatchReport, reportFileName, windowText } from "./caseTypeTrends.js";

// Case Types + EQ trends PDF. It is drawn directly from the same verified report (and AI text) that the popup shows,
// with vector charts - no screenshots - so nothing from the modal UI can leak in and nothing is recalculated here.

const PAGE = { w: 210, h: 297, margin: 14, top: 22, bottom: 18 };
const CONTENT_W = PAGE.w - PAGE.margin * 2;
const GREEN = [26, 58, 52], TEAL = [61, 112, 104], GOLD = [185, 137, 74], GREY = [183, 183, 176], TEXT = [28, 36, 34], SOFT = [96, 106, 102], RULE = [214, 210, 200], PALE = [246, 244, 238];

// jsPDF's built-in fonts only cover Latin-1 (+ a few punctuation marks); anything else would print as garbage.
const SAFE = /[^ -~ -ÿ–—‘’“”•…]/g;
export const pdfText = (value) => String(value ?? "").replace(/\s+/g, " ").replace(SAFE, "?").trim();

const INSIGHT_LABELS = [
  ["key_finding", "Key finding"], ["repeated_questions", "Repeated questions"], ["case_type_insights", "Case-type insights"],
  ["trend_interpretation", "Trend interpretation"], ["team_preparation", "Team preparation"], ["data_limitations", "Data limitations"],
];

// Weekly/daily labels already contain the date range; the month label does not, so it gets the range added.
const headerPeriod = (period) => `${period.label.replace(" Trends", "")}${period.type === "monthly" ? `  |  ${periodRangeText(period)}` : ""}`;

class Layout {
  constructor(doc, report) {
    this.doc = doc; this.report = report; this.y = PAGE.top;
    this.header();
  }
  header() {
    const { doc, report } = this;
    doc.setFont("helvetica", "bold").setFontSize(8.5).setTextColor(...GREEN).text("FlintDesk | Case Types + EQ report", PAGE.margin, 12);
    doc.setFont("helvetica", "normal").setFontSize(8).setTextColor(...SOFT)
      .text(pdfText(headerPeriod(report.period)), PAGE.w - PAGE.margin, 12, { align: "right" });
    doc.setDrawColor(...RULE).setLineWidth(0.3).line(PAGE.margin, 15, PAGE.w - PAGE.margin, 15);
  }
  newPage() { this.doc.addPage(); this.y = PAGE.top; this.header(); }
  space(height) { if (this.y + height > PAGE.h - PAGE.bottom) { this.newPage(); return true; } return false; }
  gap(mm = 4) { this.y += mm; }
  title(text, size = 13) {
    if (this.y > PAGE.top + 1) this.y += 3;     // breathing room above a heading (none at the top of a page)
    this.space(14);
    this.doc.setFont("helvetica", "bold").setFontSize(size).setTextColor(...GREEN).text(pdfText(text), PAGE.margin, this.y + 4);
    this.y += size > 14 ? 11 : 8;
  }
  hint(text) {
    const lines = this.doc.setFont("helvetica", "normal").setFontSize(8.5).splitTextToSize(pdfText(text), CONTENT_W);
    this.space(lines.length * 4 + 2);
    this.doc.setTextColor(...SOFT).text(lines, PAGE.margin, this.y + 3); this.y += lines.length * 4 + 2;
  }
  paragraph(text, { bold = false, size = 9.5, indent = 0, color = TEXT, width = CONTENT_W } = {}) {
    const lines = this.doc.setFont("helvetica", bold ? "bold" : "normal").setFontSize(size).splitTextToSize(pdfText(text), width - indent);
    const lh = size * 0.46;
    for (const line of lines) {
      this.space(lh + 1);
      this.doc.setTextColor(...color).text(line, PAGE.margin + indent, this.y + lh); this.y += lh + 0.9;
    }
  }
  bullet(text) {
    const lines = this.doc.setFont("helvetica", "normal").setFontSize(9.5).splitTextToSize(pdfText(text), CONTENT_W - 6);
    this.space(lines.length * 4.6 + 1);
    this.doc.setTextColor(...TEXT).text("•", PAGE.margin + 1, this.y + 4.2).text(lines, PAGE.margin + 6, this.y + 4.2);
    this.y += lines.length * 4.6 + 1;
  }
  // Table with a repeating header; every cell wraps. columns: [{ label, width, align }]
  table(columns, rows, { size = 8.5 } = {}) {
    const { doc } = this;
    const draw = (cells, isHeader) => {
      doc.setFont("helvetica", isHeader ? "bold" : "normal").setFontSize(size);
      const wrapped = cells.map((cell, i) => doc.splitTextToSize(pdfText(cell), columns[i].width - 3));
      const lines = Math.max(...wrapped.map((w) => w.length));
      const height = lines * (size * 0.43) + 3;
      if (this.space(height + (isHeader ? 0 : 0))) draw(columns.map((c) => c.label), true);
      if (isHeader) { doc.setFillColor(...PALE).rect(PAGE.margin, this.y, CONTENT_W, height, "F"); }
      doc.setTextColor(...(isHeader ? GREEN : TEXT));
      let x = PAGE.margin;
      wrapped.forEach((cell, i) => {
        doc.text(cell, columns[i].align === "right" ? x + columns[i].width - 1.5 : x + 1.5, this.y + size * 0.42 + 1, { align: columns[i].align === "right" ? "right" : "left" });
        x += columns[i].width;
      });
      doc.setDrawColor(...RULE).setLineWidth(0.15).line(PAGE.margin, this.y + height, PAGE.margin + CONTENT_W, this.y + height);
      this.y += height;
    };
    draw(columns.map((c) => c.label), true);
    rows.forEach((row) => draw(row, false));
    this.gap(3);
  }
  // Ranked horizontal bars. items: [{ label, value, sub, muted, highlight }]
  bars(items, max) {
    const { doc } = this;
    const labelW = 78, valueW = 26, barX = PAGE.margin + labelW + 2, barW = CONTENT_W - labelW - valueW - 4;
    const top = max || Math.max(1, ...items.map((i) => i.value));
    for (const item of items) {
      doc.setFont("helvetica", "normal").setFontSize(8.5);
      const lines = doc.splitTextToSize(pdfText(item.label), labelW).slice(0, 3);
      const height = Math.max(6.5, lines.length * 3.7 + 2.5);
      this.space(height + 1);
      doc.setTextColor(...(item.muted ? SOFT : TEXT)).text(lines, PAGE.margin, this.y + 3.8);
      const w = Math.max(1.2, (item.value / top) * barW);
      doc.setFillColor(...(item.highlight ? GOLD : item.muted ? GREY : TEAL)).rect(barX, this.y + 1.2, w, Math.min(height - 2.4, 5.5), "F");
      doc.setFont("helvetica", "bold").setTextColor(...TEXT).text(pdfText(`${item.value}${item.sub ? `  (${item.sub})` : ""}`), barX + w + 1.5, this.y + 4.8);
      this.y += height;
    }
    this.gap(2);
  }
  columns(points, { heightMm = 52 } = {}) {
    const { doc } = this;
    this.space(heightMm + 14);
    const left = PAGE.margin + 9, plotW = CONTENT_W - 10, baseY = this.y + heightMm - 8, plotH = heightMm - 16;
    const max = Math.max(1, ...points.map((p) => p.value));
    const niceMax = max <= 4 ? max : Math.ceil(max / 4) * 4;
    doc.setFont("helvetica", "normal").setFontSize(7.5).setTextColor(...SOFT);
    [0, 0.5, 1].forEach((f) => {
      const yy = baseY - f * plotH;
      doc.setDrawColor(...RULE).setLineWidth(0.15).line(left, yy, left + plotW, yy);
      doc.text(String(Math.round(niceMax * f)), left - 1.5, yy + 1, { align: "right" });
    });
    const slot = plotW / points.length, barW = Math.min(9, slot * 0.7), every = points.length <= 12 ? 1 : points.length <= 20 ? 2 : 3;
    points.forEach((p, i) => {
      const x = left + i * slot + (slot - barW) / 2;
      if (p.hatched) {
        doc.setFillColor(238, 236, 229).rect(x, baseY - 2.5, barW, 2.5, "F");
      } else if (p.value > 0) {
        const h = (p.value / niceMax) * plotH;
        doc.setFillColor(...TEAL).rect(x, baseY - h, barW, h, "F");
        doc.setFont("helvetica", "bold").setFontSize(7.5).setTextColor(...TEXT).text(String(p.value), x + barW / 2, baseY - h - 1, { align: "center" });
      }
      if (i % every === 0) doc.setFont("helvetica", "normal").setFontSize(7.5).setTextColor(...SOFT).text(pdfText(p.label), x + barW / 2, baseY + 4, { align: "center" });
    });
    doc.setDrawColor(...SOFT).setLineWidth(0.3).line(left, baseY, left + plotW, baseY);
    this.y += heightMm + 2;
  }
}

function kpiCards(layout, report) {
  const { doc } = layout;
  const topType = report.kpis.top_case_type, topQ = report.kpis.top_question;
  const cards = [
    ["Total Enquiries", String(report.kpis.total_enquiries), "calls with an enquiry question"],
    ["Most Enquired Case Type", topType ? topType.name : "None specific", topType ? `${topType.count} enquiries (${topType.share}%)` : "no specific case type recorded"],
    ["Most Repeated Question", topQ ? topQ.text : "None repeated", topQ ? `asked ${topQ.count} times` : "every question appeared once"],
    ["Distinct Questions", String(report.kpis.distinct_questions), `${report.totals.repeated_questions} repeated (${report.totals.repeated_occurrences} occurrences)`],
  ];
  const w = (CONTENT_W - 4) / 2, h = 26;
  layout.space(h * 2 + 6);
  cards.forEach(([label, value, sub], i) => {
    const x = PAGE.margin + (i % 2) * (w + 4), y = layout.y + Math.floor(i / 2) * (h + 4);
    doc.setFillColor(...PALE).setDrawColor(...RULE).roundedRect(x, y, w, h, 2, 2, "FD");
    doc.setFont("helvetica", "normal").setFontSize(8).setTextColor(...SOFT).text(pdfText(label), x + 3, y + 5.5);
    doc.setFont("helvetica", "bold").setFontSize(value.length > 28 ? 8.5 : 12).setTextColor(...GREEN);
    doc.text(doc.splitTextToSize(pdfText(value), w - 6).slice(0, 3), x + 3, y + 11);
    doc.setFont("helvetica", "normal").setFontSize(8).setTextColor(...SOFT).text(doc.splitTextToSize(pdfText(sub), w - 6).slice(0, 1), x + 3, y + h - 3);
  });
  layout.y += h * 2 + 8;
}

// All enquiry questions of the period, one table per case type (one row per call). Nothing is cut: long text wraps,
// tables continue on the next page with their header repeated, and a category heading is kept with its first rows.
function questionSection(layout, report, questions) {
  layout.newPage();
  layout.title("All enquiry questions by case type");
  const groups = groupQuestionsByCategory(questions.items, report.case_types);
  layout.hint(`${questions.total} enquiry question${questions.total === 1 ? "" : "s"} across ${groups.length} case type${groups.length === 1 ? "" : "s"}, `
    + `${report.period.start} to ${report.period.end} (Central Time). One row per call.`);
  if (!questionsMatchReport(report, questions)) {
    layout.paragraph(questions.truncated
      ? `Note: this list was shortened to ${questions.items.length} of ${questions.total} questions.`
      : `Note: the data changed while this report was being prepared (${questions.total} questions listed; the figures above counted ${report.totals.enquiries}).`,
    { size: 8.5, color: GOLD });
  }
  if (!groups.length) { layout.paragraph("No enquiry questions were recorded in this period.", { color: SOFT }); return; }
  groups.forEach((group) => {
    layout.space(34);                                              // heading + table header + a first row stay together
    layout.paragraph(`${group.name} \u2013 ${group.items.length} question${group.items.length === 1 ? "" : "s"}`, { bold: true, size: 10.5, color: GREEN });
    layout.table([{ label: "Date", width: 24 }, { label: "Call", width: 26 }, { label: "Enquiry question", width: 132 }],
      group.items.map((item) => [formatShortDate(item.date), item.call_ref || "-", item.question]));
  });
}

/**
 * Builds the report PDF. `insights` is the AI text or null; `unavailable` says why when it is null.
 * `questions` is the full question list for the same period ({ total, truncated, items }); when given, every question
 * is printed under its case type.
 */
export async function buildCaseTypeTrendsPdf(report, insights, unavailable = "", JsPdf, questions = null) {
  const Ctor = JsPdf || (await import("jspdf")).jsPDF;
  const doc = new Ctor({ orientation: "portrait", unit: "mm", format: "a4" });
  doc.setProperties({ title: `Case Types + EQ - ${report.period.label}`, subject: periodRangeText(report.period), creator: "FlintDesk" });
  const L = new Layout(doc, report);
  const p = report.period;

  // ---- Page 1: overview
  doc.setFont("helvetica", "bold").setFontSize(16).setTextColor(...GREEN).text("Case Types + Enquiry Questions \u2013 Trends Report", PAGE.margin, L.y + 6);
  doc.setFont("helvetica", "normal").setFontSize(11).setTextColor(...TEXT).text(pdfText(p.label), PAGE.margin, L.y + 13);
  L.y += 18;
  L.paragraph(`Period: ${p.type[0].toUpperCase()}${p.type.slice(1)}   |   ${periodRangeText(p)}${p.is_partial ? " (in progress)" : ""}`, { size: 9.5 });
  L.paragraph(`Reporting window: ${p.start} to ${p.end}, Central Time (America/Chicago)`, { size: 9, color: SOFT });
  L.paragraph(`Report generated: ${formatGeneratedAt(report.generated_at)}   |   Data snapshot ${report.snapshot_hash}`, { size: 9, color: SOFT });
  L.gap(4);
  kpiCards(L, report);
  L.paragraph(`Calls in scope: ${report.totals.records_in_scope}.  Enquiries: ${report.totals.enquiries}.  Not counted: ${report.totals.excluded_voicemail_spam} voicemail/spam calls and ${report.totals.without_question} calls with no enquiry question.`, { size: 9, color: SOFT });
  L.gap(2);
  L.paragraph("How to read this report: an enquiry is a call record with an enquiry question. Two enquiries that ask the same thing count as two occurrences. Questions differing only in capitalisation or punctuation count as the same question.", { size: 8.5, color: SOFT });
  if (report.notes.length) {
    L.gap(3); L.title("Reporting notes", 11);
    report.notes.forEach((note) => L.bullet(note));
  }

  if (report.totals.enquiries > 0) {
    // ---- Case types
    L.newPage();
    L.title("Most enquired case types");
    L.hint("Enquiries per case type, highest first. Grey bars are not a specific case type (Unclassified / Other).");
    L.bars(report.case_types.slice(0, 12).map((c) => ({ label: c.name, value: c.count, sub: `${c.share}%`, muted: !c.classified, highlight: c.classified && c.name === report.kpis.top_case_type?.name })));
    L.table([{ label: "Case type", width: 110 }, { label: "Enquiries", width: 36, align: "right" }, { label: "Share", width: 36, align: "right" }],
      report.case_types.map((c) => [c.name, String(c.count), `${c.share}%`]));

    // ---- Repeated questions
    L.title("Most repeated enquiry questions");
    L.hint(`${report.totals.enquiries} enquiries contain ${report.totals.distinct_questions} distinct questions; ${report.totals.repeated_questions} were asked more than once.`);
    if (report.repeated_questions.length) {
      L.bars(report.repeated_questions.map((q) => ({ label: q.text, value: q.count })));
      L.table([{ label: "Question", width: 112 }, { label: "Times asked", width: 28, align: "right" }, { label: "Main case type", width: 42 }],
        report.repeated_questions.map((q) => [q.text, String(q.count), q.case_types[0] || ""]));
    } else {
      L.paragraph("No enquiry question was repeated in this period.", { color: SOFT });
    }

    // ---- Within each case type (only questions asked more than once are listed)
    L.title("Repeated questions within each case type");
    L.hint("Questions asked more than once, by case type. Questions asked once are counted but not listed.");
    const typed = report.by_case_type.filter((c) => c.classified);
    const withRepeats = typed.filter((c) => c.top_questions.length);
    withRepeats.slice(0, 6).forEach((c) => {
      L.space(34);
      L.paragraph(`${c.name}: ${c.count} enquiries, ${c.distinct_questions} distinct questions, ${c.repeated_questions} repeated`, { bold: true, size: 9.5, color: GREEN });
      L.table([{ label: "Question", width: 150 }, { label: "Times asked", width: 32, align: "right" }], c.top_questions.map((q) => [q.text, String(q.count)]));
    });
    if (!typed.length) L.paragraph("No specific case types were recorded in this period.", { color: SOFT });
    else if (!withRepeats.length) L.paragraph("No question was repeated within any case type in this period.", { color: SOFT });
    const quiet = typed.filter((c) => !c.top_questions.length);
    if (withRepeats.length && quiet.length) L.paragraph(`No repeated questions: ${quiet.map((c) => `${c.name} (${c.count} enquiries)`).join(", ")}.`, { size: 8.5, color: SOFT });

    // ---- Volume over time
    L.space(86);                                       // keep the title, hint and chart together
    L.title(p.type === "daily" ? "Enquiries by hour of the day" : "Enquiry volume over time");
    L.hint(p.type === "daily" ? `${report.totals.enquiries} enquiries on ${periodRangeText(p)}.` : "Hatched / light bars are days with no calls recorded (not the same as zero enquiries). Days not yet reached are not shown.");
    const points = p.type === "daily" ? report.hourly.map((h) => ({ label: h.label, value: h.enquiries }))
      : report.series.map((d) => ({ label: d.label.replace(/^\w+ /, ""), value: d.enquiries, hatched: d.no_calls_recorded }));
    L.columns(points);
    if (p.type !== "daily") {
      L.table([{ label: "Day", width: 52 }, { label: "Enquiries", width: 40, align: "right" }, { label: "Calls recorded", width: 44, align: "right" }, { label: "Note", width: 46 }],
        report.series.map((d) => [d.label, d.no_calls_recorded ? "–" : String(d.enquiries), String(d.records), d.no_calls_recorded ? "no calls recorded" : ""]));
    }
    const c = report.comparison;
    L.paragraph(c.comparable
      ? `Compared with ${windowText(c.window)}: ${c.enquiries} vs ${c.previous_enquiries} enquiries (${c.change > 0 ? "+" : ""}${c.change}${c.change_pct != null ? `, ${c.change_pct > 0 ? "+" : ""}${c.change_pct}%` : ""})${c.small_sample ? " - small sample, interpret with care." : "."}`
      : "No comparable earlier period with enquiries is available, so no comparison is shown.", { size: 9, color: SOFT });

    // ---- Every enquiry question, under its case type
    if (questions) questionSection(L, report, questions);

    // ---- AI insights
    L.newPage();
    L.title("AI-Generated Insights");
    L.hint("Written by AI from the verified figures in this report. All counts and rankings above are calculated by the application, not by the AI.");
    if (insights) {
      INSIGHT_LABELS.forEach(([key, label]) => {
        L.space(14); L.paragraph(label, { bold: true, size: 10, color: GREEN });
        const value = insights[key];
        if (Array.isArray(value)) value.forEach((line) => L.bullet(line)); else L.paragraph(value);
        L.gap(2);
      });
    } else {
      L.space(20);
      doc.setFillColor(...PALE).setDrawColor(...RULE).roundedRect(PAGE.margin, L.y, CONTENT_W, 18, 2, 2, "FD");
      doc.setFont("helvetica", "normal").setFontSize(9.5).setTextColor(...TEXT)
        .text(doc.splitTextToSize(pdfText(`AI insights were not available when this report was generated. ${unavailable || ""} The verified figures and charts in this report are complete.`), CONTENT_W - 8), PAGE.margin + 4, L.y + 6);
      L.y += 22;
    }
  }

  // ---- Footer on every page
  const pages = doc.getNumberOfPages();
  for (let i = 1; i <= pages; i += 1) {
    doc.setPage(i);
    doc.setDrawColor(...RULE).setLineWidth(0.3).line(PAGE.margin, PAGE.h - 13, PAGE.w - PAGE.margin, PAGE.h - 13);
    doc.setFont("helvetica", "normal").setFontSize(8).setTextColor(...SOFT)
      .text(pdfText(`Generated ${formatGeneratedAt(report.generated_at)}`), PAGE.margin, PAGE.h - 8)
      .text(`Page ${i} of ${pages}`, PAGE.w - PAGE.margin, PAGE.h - 8, { align: "right" });
  }
  return doc;
}

export async function downloadCaseTypeTrendsPdf(report, insights, unavailable = "", questions = null) {
  const doc = await buildCaseTypeTrendsPdf(report, insights, unavailable, undefined, questions);
  doc.save(reportFileName(report));
}
