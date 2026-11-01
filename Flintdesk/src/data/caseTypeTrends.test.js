import test from "node:test";
import assert from "node:assert/strict";

import {
  groupQuestionsByCategory, questionsMatchReport, formatShortDate, formatAsOf, describeCoverage,
  barPercent, formatGeneratedAt, formatIsoDate, hasEnquiries, labelEvery, limitItems, periodRangeText, reportFileName, windowText,
} from "./caseTypeTrends.js";
import { buildCaseTypeTrendsPdf, pdfText } from "./caseTypeTrendsPdf.js";

const period = (type, start, end, extra = {}) => ({ type, start, end, label: `${type} label`, is_partial: false, ...extra });

test("period text uses the calendar dates as given, whatever the browser timezone", () => {
  const previous = process.env.TZ;
  for (const zone of ["UTC", "America/Chicago", "Pacific/Auckland"]) {
    process.env.TZ = zone;
    assert.equal(formatIsoDate("2026-10-11"), "October 11, 2026");
    assert.equal(periodRangeText(period("daily", "2026-10-11", "2026-10-11")), "October 11, 2026");
    assert.equal(periodRangeText(period("weekly", "2026-10-05", "2026-10-11")), "October 5 – October 11, 2026");
    assert.equal(periodRangeText(period("weekly", "2026-12-28", "2027-01-03")), "December 28, 2026 – January 3, 2027");
  }
  process.env.TZ = previous;
  assert.equal(formatIsoDate("garbage"), "");
});

test("generated time is shown as the Central wall-clock time the server gave", () => {
  assert.equal(formatGeneratedAt("2026-10-11T18:05:00-05:00"), "October 11, 2026, 6:05 PM CT");
  assert.equal(formatGeneratedAt("2026-10-11T00:07:00-05:00"), "October 11, 2026, 12:07 AM CT");
  assert.equal(formatGeneratedAt(""), "");
});

test("file names reflect the selected period", () => {
  assert.equal(reportFileName({ period: period("daily", "2026-10-11", "2026-10-11") }), "Case_Types_EQ_Daily_2026-10-11.pdf");
  assert.equal(reportFileName({ period: period("weekly", "2026-10-05", "2026-10-11") }), "Case_Types_EQ_Weekly_2026-10-05_to_2026-10-11.pdf");
  assert.equal(reportFileName({ period: period("monthly", "2026-10-01", "2026-10-31") }), "Case_Types_EQ_Monthly_2026-10.pdf");
});

test("chart helpers", () => {
  assert.equal(barPercent(10, 10), 100);
  assert.equal(barPercent(1, 1000), 2);          // tiny values stay visible
  assert.equal(barPercent(0, 10), 0);
  assert.equal(barPercent(5, 0), 0);
  assert.equal(limitItems([1, 2, 3], false, 2).length, 2);
  assert.equal(limitItems([1, 2, 3], true, 2).length, 3);
  assert.deepEqual([labelEvery(7), labelEvery(16), labelEvery(31)], [1, 2, 5]);
  assert.equal(hasEnquiries({ totals: { enquiries: 0 } }), false);
  assert.equal(hasEnquiries(null), false);
});

test("PDF text never carries characters the built-in font cannot draw", () => {
  assert.equal(pdfText("Café — “quoted” …"), "Café — “quoted” …");
  assert.equal(pdfText("Hello தமிழ் world"), "Hello ????? world");
  assert.equal(pdfText("  a \n\t b  "), "a b");
});

// ---- a recording stand-in for jsPDF, so the report content can be asserted without a binary PDF
class FakePdf {
  constructor() { this.pages = [[]]; this.current = 0; this.saved = null; this.properties = null; }
  rec(kind, ...a) { this.pages[this.current].push([kind, ...a]); return this; }
  setProperties(p) { this.properties = p; return this; }
  setFont() { return this; } setFontSize() { return this; } setTextColor() { return this; } setFillColor() { return this; }
  setDrawColor() { return this; } setLineWidth() { return this; }
  text(t, x, y, o) { return this.rec("text", Array.isArray(t) ? t.join("\n") : String(t), x, y, o); }
  line(...a) { return this.rec("line", ...a); } rect(...a) { return this.rec("rect", ...a); } roundedRect(...a) { return this.rec("rrect", ...a); }
  splitTextToSize(t, w) { const per = Math.max(8, Math.floor(w * 2.2)); const out = []; for (let i = 0; i < t.length; i += per) out.push(t.slice(i, i + per)); return out.length ? out : [""]; }
  addPage() { this.pages.push([]); this.current = this.pages.length - 1; return this; }
  getNumberOfPages() { return this.pages.length; } setPage(n) { this.current = n - 1; return this; }
  allText(page) { return (page === undefined ? this.pages.flat() : this.pages[page]).filter((e) => e[0] === "text").map((e) => e[1]).join("\n"); }
  rects() { return this.pages.flat().filter((e) => e[0] === "rect"); }
}

const report = {
  period: { type: "weekly", start: "2026-10-05", end: "2026-10-11", label: "Weekly Trends — October 5 – October 11, 2026", is_partial: false, timezone: "America/Chicago" },
  generated_at: "2026-10-11T18:05:00-05:00", snapshot_hash: "abc123def4567890",
  totals: { records_in_scope: 120, enquiries: 80, excluded_voicemail_spam: 25, without_question: 15, distinct_questions: 70, repeated_questions: 4, repeated_occurrences: 12 },
  kpis: { total_enquiries: 80, top_case_type: { name: "H-1B", count: 30, share: 37.5 }, top_question: { text: "Is my H-1B extension approved?", count: 4 }, distinct_questions: 70 },
  case_types: [{ name: "H-1B", count: 30, share: 37.5, classified: true }, { name: "Unclassified", count: 40, share: 50, classified: false }, { name: "F-1 / OPT / STEM", count: 10, share: 12.5, classified: true }],
  questions: [], repeated_questions: [{ text: "Is my H-1B extension approved?", count: 4, case_types: ["H-1B"] }, { text: "x ".repeat(300), count: 2, case_types: ["F-1 / OPT / STEM"] }],
  by_case_type: [{ name: "H-1B", count: 30, classified: true, distinct_questions: 25, repeated_questions: 2, top_questions: [{ text: "Is my H-1B extension approved?", count: 4 }] },
                 { name: "Unclassified", count: 40, classified: false, distinct_questions: 38, repeated_questions: 1, top_questions: [] }],
  series: [{ date: "2026-10-05", label: "Mon 10/5", enquiries: 20, records: 30, no_calls_recorded: false }, { date: "2026-10-10", label: "Sat 10/10", enquiries: 0, records: 0, no_calls_recorded: true }],
  hourly: [], comparison: { comparable: true, small_sample: false, enquiries: 80, previous_enquiries: 60, change: 20, change_pct: 33.3, window: { start: "2026-09-28", end: "2026-10-04" } },
  notes: ["40 of 80 enquiries (50.0%) have no specific case type (Unclassified), so case-type counts understate the real mix."],
};
const insights = { key_finding: "H-1B led with 30.", repeated_questions: "One question repeated 4 times.", case_type_insights: ["H-1B is highest."], trend_interpretation: "Up from 60 to 80.", team_preparation: ["Prepare an H-1B FAQ."], data_limitations: "Unclassified is large." };

test("the PDF carries the selected period, KPIs, every chart section and the AI insights", async () => {
  const doc = await buildCaseTypeTrendsPdf(report, insights, "", FakePdf);
  const all = doc.allText();
  for (const expected of ["Weekly Trends — October 5 – October 11, 2026", "October 5 – October 11, 2026", "America/Chicago", "October 11, 2026, 6:05 PM CT",
    "Total Enquiries", "Most Enquired Case Type", "Most Repeated Question", "Distinct Questions", "Most enquired case types", "Most repeated enquiry questions",
    "Repeated questions within each case type", "Enquiry volume over time", "AI-Generated Insights", "H-1B led with 30.", "Prepare an H-1B FAQ.", "Reporting notes"]) {
    assert.ok(all.includes(expected), `missing: ${expected}`);
  }
  assert.ok(doc.rects().length > 5, "charts are drawn as shapes");
});

test("every page has the period header and a numbered footer", async () => {
  const doc = await buildCaseTypeTrendsPdf(report, insights, "", FakePdf);
  assert.ok(doc.getNumberOfPages() >= 3);
  doc.pages.forEach((_, i) => {
    const text = doc.allText(i);
    assert.ok(text.includes("October 5 – October 11, 2026"), `page ${i + 1} has no period header`);
    assert.ok(text.includes(`Page ${i + 1} of ${doc.getNumberOfPages()}`), `page ${i + 1} has no page number`);
  });
});

test("without AI insights the PDF says so instead of inventing any", async () => {
  const doc = await buildCaseTypeTrendsPdf(report, null, "AI insights are temporarily unavailable.", FakePdf);
  const all = doc.allText();
  assert.ok(all.includes("AI insights were not available when this report was generated"));
  assert.ok(!all.includes("H-1B led with 30."));
  assert.ok(all.includes("Most enquired case types"), "verified charts are still there");
});

test("an empty period gives a short, honest report with no chart sections", async () => {
  const empty = { ...report, totals: { ...report.totals, enquiries: 0, distinct_questions: 0, repeated_questions: 0, repeated_occurrences: 0 },
    kpis: { total_enquiries: 0, top_case_type: null, top_question: null, distinct_questions: 0 }, case_types: [], notes: ["No enquiries were recorded for this period."] };
  const doc = await buildCaseTypeTrendsPdf(empty, null, "", FakePdf);
  const all = doc.allText();
  assert.ok(all.includes("No enquiries were recorded for this period.") && !all.includes("Most enquired case types"));
});

test("very long question text is wrapped inside the page, not run off it", async () => {
  const doc = await buildCaseTypeTrendsPdf(report, insights, "", FakePdf);
  for (const [kind, text, x] of doc.pages.flat().filter((e) => e[0] === "text")) {
    assert.ok(Number.isFinite(x) && x >= 0 && x <= 210, `${kind} at x=${x}`);
    assert.ok(!/ {3,}/.test(text) || text.length < 400);
  }
});

test("comparison windows read naturally", () => {
  assert.equal(windowText({ start: "2026-10-08", end: "2026-10-08" }), "October 8, 2026");
  assert.equal(windowText({ start: "2026-09-28", end: "2026-10-04" }), "September 28 – October 4, 2026");
  assert.equal(windowText({ start: "2026-12-28", end: "2027-01-03" }), "December 28, 2026 – January 3, 2027");
  assert.equal(windowText(null), "");
});

test("questions are grouped by case type in report order, with new types added after, and date order kept", () => {
  const types = [{ name: "H-1B" }, { name: "F-1 / OPT / STEM" }, { name: "Asylum" }];
  const items = [
    { case_type: "F-1 / OPT / STEM", date: "2026-10-05", question: "a" }, { case_type: "H-1B", date: "2026-10-05", question: "b" },
    { case_type: "TN visa", date: "2026-10-06", question: "c" }, { case_type: "H-1B", date: "2026-10-07", question: "d" },
  ];
  const groups = groupQuestionsByCategory(items, types);
  assert.deepEqual(groups.map((g) => g.name), ["H-1B", "F-1 / OPT / STEM", "TN visa"]);   // Asylum has none: not shown
  assert.deepEqual(groups[0].items.map((i) => i.question), ["b", "d"]);
  assert.deepEqual(groupQuestionsByCategory([], types), []);
  assert.deepEqual(groupQuestionsByCategory(undefined, undefined), []);
});

test("the question list reconciles with the report only when totals match and nothing was cut off", () => {
  const report = { totals: { enquiries: 4 } };
  assert.equal(questionsMatchReport(report, { total: 4, truncated: false, items: [] }), true);
  assert.equal(questionsMatchReport(report, { total: 5, truncated: false }), false);
  assert.equal(questionsMatchReport(report, { total: 4, truncated: true }), false);
  assert.equal(questionsMatchReport(null, { total: 4 }), false);
});

test("short dates are formatted by parts", () => {
  assert.equal(formatShortDate("2026-10-05"), "10/05/2026");
  assert.equal(formatShortDate("2026-10-05T23:30:00"), "10/05/2026");
  assert.equal(formatShortDate(""), "");
});

// ---- the full question list in the PDF
const manyQuestions = (() => {
  const items = [];
  const types = ["H-1B", "Unclassified", "F-1 / OPT / STEM", "TN visa"];          // "TN visa" is not in the report's list: it must still print
  for (let i = 1; i <= 160; i += 1) {
    items.push({ date: `2026-10-${String(5 + (i % 6)).padStart(2, "0")}`, call_ref: `AF#${1000 + i}`, case_type: types[i % 4], question: `QUESTION-${String(i).padStart(3, "0")} ${"how long will it take ".repeat(i % 5 === 0 ? 12 : 1)}` });
  }
  return { total: 160, truncated: false, items };
})();
const reportFor160 = { ...report, totals: { ...report.totals, enquiries: 160 } };

test("the PDF prints every question under its case type, across pages, nothing cut", async () => {
  const doc = await buildCaseTypeTrendsPdf(reportFor160, insights, "", FakePdf, manyQuestions);
  const all = doc.allText();
  for (const item of manyQuestions.items) assert.ok(all.includes(item.question.split(" ")[0]), `missing ${item.question.split(" ")[0]}`);
  assert.ok(all.includes("All enquiry questions by case type") && all.includes("160 enquiry questions across 4 case types"));
  for (const name of ["H-1B", "Unclassified", "F-1 / OPT / STEM", "TN visa"]) assert.ok(all.includes(`${name} – 40 questions`), `heading for ${name}`);
  const long = manyQuestions.items.find((i) => i.question.startsWith("QUESTION-005"));
  const joined = all.replace(/\n/g, "");
  assert.ok(joined.includes(long.question.replace(/\s+/g, " ").trim().slice(0, 60)));
});

test("the question tables repeat their header on every page they continue onto", async () => {
  const doc = await buildCaseTypeTrendsPdf(reportFor160, null, "", FakePdf, manyQuestions);
  const pagesWithRows = doc.pages.map((_, i) => doc.allText(i)).filter((t) => /QUESTION-\d{3}/.test(t));
  assert.ok(pagesWithRows.length >= 3, "160 questions need several pages");
  pagesWithRows.forEach((t) => assert.ok(t.includes("Enquiry question") && t.includes("Date"), "table header on each page"));
});

test("a question list that does not match the report is flagged, not hidden", async () => {
  const changed = await buildCaseTypeTrendsPdf(report, null, "", FakePdf, { total: 81, truncated: false, items: [{ date: "2026-10-05", call_ref: "AF#1", case_type: "H-1B", question: "One" }] });
  assert.ok(changed.allText().includes("the data changed while this report was being prepared"));
  const cut = await buildCaseTypeTrendsPdf(reportFor160, null, "", FakePdf, { total: 160, truncated: true, items: manyQuestions.items.slice(0, 5) });
  assert.ok(cut.allText().includes("shortened to 5 of 160"));
});

test("an empty question list says so, and no list at all adds no section", async () => {
  const none = await buildCaseTypeTrendsPdf(report, null, "", FakePdf, { total: 0, truncated: false, items: [] });
  assert.ok(none.allText().includes("No enquiry questions were recorded in this period."));
  const without = await buildCaseTypeTrendsPdf(report, null, "", FakePdf);
  assert.ok(!without.allText().includes("All enquiry questions by case type"));
  assert.ok(without.allText().includes("Case Types + Enquiry Questions"));
});

test("the update time is shown in Central Time and in the viewer's own zone for the same instant", () => {
  const both = formatAsOf("2026-10-12T16:34:00-05:00", "Asia/Kolkata");
  assert.equal(both.ct, "4:34 PM CDT");
  assert.equal(both.local, "3:04 AM GMT+5:30");
  assert.equal(formatAsOf("2026-01-15T10:00:00-06:00", "Asia/Kolkata").ct, "10:00 AM CST");     // winter: standard time
  assert.equal(formatAsOf("", "UTC"), null);
  assert.equal(formatAsOf("not a date"), null);
});

test("the coverage sentence accounts for every call record and leaves out zero parts", () => {
  const summary = { callRecords: 939, enquiryQuestions: 412, voicemailOrSpam: 85, noQuestionRecorded: 330, otherRecords: 110, waitingForAnalysis: 2 };
  assert.equal(describeCoverage(summary),
    "939 call records in this date range reviewed: 412 with an enquiry question (listed below) · 85 voicemail or spam · 330 with no enquiry question recorded · 110 other records not counted as enquiries · 2 waiting for AI analysis.");
  const parts = [summary.enquiryQuestions, summary.voicemailOrSpam, summary.noQuestionRecorded, summary.otherRecords, summary.waitingForAnalysis];
  assert.equal(parts.reduce((a, b) => a + b, 0), summary.callRecords);
  assert.equal(describeCoverage({ callRecords: 1, enquiryQuestions: 1, voicemailOrSpam: 0, noQuestionRecorded: 0, otherRecords: 0, waitingForAnalysis: 0 }),
    "1 call record in this date range reviewed: 1 with an enquiry question (listed below).");
  assert.equal(describeCoverage({ callRecords: 0 }), "0 call records in this date range reviewed.");
  assert.equal(describeCoverage(null), "");
  assert.equal(describeCoverage({}), "");
});
