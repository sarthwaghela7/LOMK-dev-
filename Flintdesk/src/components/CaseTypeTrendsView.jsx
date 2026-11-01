import { useCallback, useEffect, useRef, useState } from "react";
import { getCaseTypeTrendInsights, getCaseTypeTrendQuestions, getCaseTypeTrends } from "../data/api.js";
import { startPolling } from "../data/polling.js";
import {
  PERIOD_OPTIONS, formatGeneratedAt, hasEnquiries, limitItems, periodRangeText, reportFileName, windowText,
} from "../data/caseTypeTrends.js";
import { RankedBars, ColumnChart } from "./TrendCharts.jsx";
import { DateRangeSelector } from "./DateRangeSelector.jsx";
import TrendDetailModal from "./TrendDetailModal.jsx";

const INSIGHT_LABELS = [
  ["key_finding", "Key finding"], ["repeated_questions", "Repeated questions"], ["case_type_insights", "Case-type insights"],
  ["trend_interpretation", "Trend interpretation"], ["team_preparation", "Team preparation"], ["data_limitations", "Data limitations"],
];

/**
 * Trends view of the Case Types + EQ page (was a popup; now shown inline). The report (all numbers, rankings, dates) comes from the server;
 * the AI text is a separate, clearly labelled section. The PDF is built from exactly the report + insights shown here.
 */
const REFRESH_MS = 45_000;                                  // live refresh while this view is on screen

// The case type shown in the "repeated questions within a case type" picker: keep the user's choice when it still exists.
const pickType = (data, current) => (data.by_case_type.some((c) => c.name === current) ? current
  : data.by_case_type.find((c) => c.classified)?.name || data.by_case_type[0]?.name || "");

export default function CaseTypeTrendsView({ initialRange, active = true }) {
  const [period, setPeriod] = useState("daily");
  const [anchor, setAnchor] = useState(null);                 // null = today in Central time (decided by the server)
  const [customDraft, setCustomDraft] = useState(() => ({ start: initialRange?.start || "", end: initialRange?.end || "" }));
  const [customRange, setCustomRange] = useState(null);       // applied custom range; the report loads only after Apply
  const [detail, setDetail] = useState(null);                 // { kind: "caseType" | "question", ... } for the click-through popup
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [insights, setInsights] = useState({ status: "idle" });   // idle | loading | ready | unavailable
  const [showAllTypes, setShowAllTypes] = useState(false);
  const [selectedType, setSelectedType] = useState("");
  const [pdf, setPdf] = useState({ busy: false, error: "" });
  const [updatedAt, setUpdatedAt] = useState(null);           // last successful refresh, shown as "Updated hh:mm"
  const requestId = useRef(0);
  const insightsPromise = useRef(null);
  const latest = useRef({});
  latest.current = { report, insights, detail, pdfBusy: pdf.busy, loading, active };

  // AI wording of a report. Started only for a new report (never on an unchanged refresh), so it is not re-requested needlessly.
  const startInsights = useCallback((data, id) => {
    if (!hasEnquiries(data)) { setInsights({ status: "idle" }); insightsPromise.current = null; return; }
    setInsights({ status: "loading" });
    insightsPromise.current = getCaseTypeTrendInsights(period, anchor, customRange).then((result) => {
      if (id !== requestId.current) return;
      const next = result.available && result.snapshot_hash === data.snapshot_hash
        ? { status: "ready", data: result.insights, generatedAt: result.generated_at }
        : { status: "unavailable", message: result.available ? "The data changed while the analysis was being prepared. It refreshes automatically." : result.message };
      latest.current.insights = next; setInsights(next);
    }).catch(() => {
      if (id !== requestId.current) return;
      const next = { status: "unavailable", message: "AI insights are temporarily unavailable." };
      latest.current.insights = next; setInsights(next);
    });
  }, [period, anchor, customRange]);

  const load = useCallback(async () => {
    if (period === "custom" && !customRange) { setReport(null); setLoading(false); setError(""); setInsights({ status: "idle" }); return; }
    const id = ++requestId.current;                         // a slower, older response never overwrites a newer one
    setLoading(true); setError(""); setInsights({ status: "idle" }); insightsPromise.current = null;
    try {
      const data = await getCaseTypeTrends(period, anchor, customRange);
      if (id !== requestId.current) return;
      setReport(data); setLoading(false); setUpdatedAt(new Date());
      setSelectedType((current) => pickType(data, current));
      startInsights(data, id);
    } catch (failure) {
      if (id !== requestId.current) return;
      setReport(null); setLoading(false); setError(failure?.message || "Unable to load trends.");
    }
  }, [period, anchor, customRange, startInsights]);

  useEffect(() => { load(); }, [load]);

  // Silent refresh: keeps what is on screen, replaces it only when the server's data actually changed, and leaves
  // the existing figures alone if the request fails. The AI text is requested again only for a changed report.
  const refresh = useCallback(async () => {
    const before = latest.current.report;
    if (!before || (period === "custom" && !customRange)) return;
    const id = requestId.current;
    try {
      const data = await getCaseTypeTrends(period, anchor, customRange);
      if (id !== requestId.current) return;                 // the user picked another period meanwhile
      setUpdatedAt(new Date());
      if (data.snapshot_hash === latest.current.report?.snapshot_hash) return;
      const next = ++requestId.current;
      setReport(data); setSelectedType((current) => pickType(data, current));
      startInsights(data, next);
    } catch { /* keep the figures already shown */ }
  }, [period, anchor, customRange, startInsights]);

  useEffect(() => {
    const poll = startPolling({
      run: refresh, intervalMs: REFRESH_MS,
      // Not while hidden, not behind another page/view, not while a popup is open or a PDF is being built or a load runs.
      isActive: () => document.visibilityState === "visible" && latest.current.active && !latest.current.detail
        && !latest.current.pdfBusy && !latest.current.loading,
    });
    const onVisible = () => { if (document.visibilityState === "visible" && latest.current.active) poll.catchUp(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { poll.stop(); document.removeEventListener("visibilitychange", onVisible); };
  }, [refresh]);

  // Coming back to this view (or page): show the latest straight away.
  useEffect(() => { if (active) refresh(); }, [active, refresh]);

  const choosePeriod = (value) => { if (value !== period) { setPeriod(value); setAnchor(null); setShowAllTypes(false); setDetail(null); } };
  const customValid = Boolean(customDraft.start && customDraft.end && customDraft.start <= customDraft.end);

  // Waits for an in-flight analysis so the PDF includes it, then builds the PDF from the data shown on screen.
  const downloadPdf = async () => {
    if (!report || pdf.busy) return;
    setPdf({ busy: true, error: "" });
    const snapshot = report;
    try {
      if (insightsPromise.current) await insightsPromise.current;
      // Every question of this period, so the PDF lists them all (the report itself only carries rankings).
      const questions = await getCaseTypeTrendQuestions(period, anchor, customRange);
      const { downloadCaseTypeTrendsPdf } = await import("../data/caseTypeTrendsPdf.js");
      const current = latest.current.insights;
      await downloadCaseTypeTrendsPdf(snapshot, current.status === "ready" ? current.data : null,
        current.status === "unavailable" ? current.message : (current.status === "idle" ? "No enquiries to analyse." : ""), questions);
      setPdf({ busy: false, error: "" });
    } catch {
      setPdf({ busy: false, error: "The PDF could not be created. Please try again." });
    }
  };

  const p = report?.period;
  const types = report?.case_types || [];
  const typeItems = limitItems(types, showAllTypes).map((c) => ({
    label: c.name, value: c.count, sub: `${c.share}%`, muted: !c.classified, highlight: c.classified && c.name === report.kpis.top_case_type?.name,
    title: `${c.name}: ${c.count} enquiries (${c.share}%) — click for details`,
    onSelect: () => setDetail({ kind: "caseType", name: c.name }),
  }));
  const repeated = (report?.repeated_questions || []).map((q) => ({ label: q.text, value: q.count, title: `${q.text} — click for details`, sub: q.case_types[0],
    onSelect: () => setDetail({ kind: "question", text: q.text }) }));
  const typeDetail = report?.by_case_type.find((c) => c.name === selectedType);
  const series = (p?.type === "daily" ? (report?.hourly || []).map((h) => ({ label: h.label, value: h.enquiries }))
    : (report?.series || []).map((d) => ({ label: d.label.replace(/^\w+ /, ""), value: d.enquiries, hatched: d.no_calls_recorded })));
  const cmp = report?.comparison;

  return (
    <section className="trends-panel" aria-labelledby="trends-title">
        <header className="trends-header">
          <div>
            <h2 id="trends-title">{p ? p.label : period === "custom" ? "Custom Trends — choose a date range" : "Case Types + EQ Trends"}</h2>
            {p && <p className="trends-sub">{periodRangeText(p)} · Central Time (America/Chicago) · <span className={`trends-status ${p.is_partial ? "open" : "closed"}`}>{p.is_partial ? "Period in progress" : "Complete period"}</span>
              {updatedAt && <span className="trends-updated" title="Refreshes automatically about every 45 seconds"> · Updated {updatedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}</p>}
          </div>
          <div className="trends-header-actions">
            <button type="button" className="table-action" onClick={downloadPdf} disabled={!report || loading || pdf.busy}>
              <i className="fa-solid fa-file-pdf" /> {pdf.busy ? "Preparing PDF…" : "Download PDF"}
            </button>
          </div>
        </header>

        <div className="trends-controls">
          <div className="trends-tabs" role="tablist" aria-label="Reporting period">
            {PERIOD_OPTIONS.map((option) => (
              <button key={option.value} type="button" role="tab" aria-selected={period === option.value}
                className={period === option.value ? "active" : ""} onClick={() => choosePeriod(option.value)}>{option.label}</button>
            ))}
          </div>
          {period !== "custom" ? (
            <div className="trends-nav">
              <button type="button" className="table-action" disabled={!p || loading} onClick={() => setAnchor(p.prev_anchor)} aria-label="Previous period">‹ Previous</button>
              <button type="button" className="table-action" disabled={!p || loading || !p.next_anchor} onClick={() => setAnchor(p.next_anchor)}
                aria-label="Next period" title={p && !p.next_anchor ? "Future periods are not available" : undefined}>Next ›</button>
              {anchor && <button type="button" className="table-action" onClick={() => setAnchor(null)} disabled={loading}>{period === "daily" ? "Today" : period === "weekly" ? "This week" : "This month"}</button>}
            </div>
          ) : (
            <div className="trends-custom">
              <DateRangeSelector value={customDraft} onChange={setCustomDraft} />
              <button type="button" className="primary-button calls-toolbar-apply" disabled={!customValid || loading}
                onClick={() => setCustomRange({ ...customDraft })}>Apply</button>
            </div>
          )}
        </div>
        {pdf.error && <p className="trends-error" role="alert">{pdf.error} <button type="button" className="calls-save-retry" onClick={downloadPdf}>Retry</button></p>}

        <div className="trends-body" aria-busy={loading}>
          {period === "custom" && !customRange && !loading && <p className="trends-state" role="status">Pick a start and end date (up to 92 days), then press Apply.</p>}
          {loading && <p className="trends-state" role="status">Loading trends…</p>}
          {!loading && error && <p className="trends-state trends-error" role="alert">{error} <button type="button" className="calls-save-retry" onClick={load}>Retry</button></p>}
          {!loading && report && !hasEnquiries(report) && (
            <div className="trends-state" role="status">
              <strong>No enquiries to show for this period.</strong>
              {report.notes.map((note) => <p key={note}>{note}</p>)}
            </div>
          )}
          {!loading && report && hasEnquiries(report) && (
            <>
              <div className="trends-kpis">
                <article><span>Total Enquiries</span><strong>{report.kpis.total_enquiries}</strong><small>calls with an enquiry question</small></article>
                <article><span>Most Enquired Case Type</span><strong>{report.kpis.top_case_type ? report.kpis.top_case_type.name : "—"}</strong>
                  <small>{report.kpis.top_case_type ? `${report.kpis.top_case_type.count} enquiries (${report.kpis.top_case_type.share}%)` : "no specific case type recorded"}</small></article>
                <article><span>Most Repeated Question</span><strong className="trends-kpi-text" title={report.kpis.top_question?.text}>{report.kpis.top_question ? report.kpis.top_question.text : "None repeated"}</strong>
                  <small>{report.kpis.top_question ? `asked ${report.kpis.top_question.count} times` : "every question appeared once"}</small></article>
                <article><span>Distinct Questions</span><strong>{report.kpis.distinct_questions}</strong>
                  <small>{report.totals.repeated_questions} repeated ({report.totals.repeated_occurrences} occurrences)</small></article>
              </div>

              {report.notes.length > 0 && (
                <aside className="trends-notes"><strong>Reporting notes</strong><ul>{report.notes.map((note) => <li key={note}>{note}</li>)}</ul></aside>
              )}

              <section className="trends-section">
                <h3>Most enquired case types</h3>
                <p className="trends-hint">Enquiries per case type, highest first. Grey bars are not a specific case type.</p>
                <RankedBars items={typeItems} />
                {types.length > 8 && <button type="button" className="trends-more" onClick={() => setShowAllTypes((v) => !v)}>{showAllTypes ? "Show top 8" : `Show all ${types.length}`}</button>}
              </section>

              <section className="trends-section">
                <h3>Most repeated enquiry questions</h3>
                <p className="trends-hint">Questions asked more than once. {report.totals.enquiries} enquiries contain {report.totals.distinct_questions} distinct questions.</p>
                <RankedBars items={repeated} emptyText="No enquiry question was repeated in this period." />
              </section>

              <section className="trends-section">
                <h3>Repeated questions within a case type</h3>
                <label className="trends-select">Case type
                  <select value={selectedType} onChange={(event) => setSelectedType(event.target.value)}>
                    {report.by_case_type.map((c) => <option key={c.name} value={c.name}>{c.name} ({c.count})</option>)}
                  </select>
                </label>
                {typeDetail && (
                  <>
                    <p className="trends-hint">{typeDetail.count} enquiries · {typeDetail.distinct_questions} distinct questions · {typeDetail.repeated_questions} repeated. Questions asked once are counted, not listed.</p>
                    <RankedBars items={typeDetail.top_questions.map((q) => ({ label: q.text, value: q.count, title: q.text }))} max={typeDetail.top_questions[0]?.count} emptyText="No question was repeated in this case type." />
                  </>
                )}
              </section>

              <section className="trends-section">
                <h3>{p.type === "daily" ? "Enquiries by hour of the day" : "Enquiry volume over time"}</h3>
                <p className="trends-hint">{p.type === "daily" ? `${report.totals.enquiries} enquiries on this day.` : "Hatched days have no calls recorded; days not yet reached are not shown."}</p>
                <ColumnChart points={series} ariaLabel={p.type === "daily" ? "Enquiries by hour" : "Enquiries per day"} />
                {cmp.comparable && (
                  <p className="trends-compare">Compared with {windowText(cmp.window)}: {cmp.enquiries} vs {cmp.previous_enquiries} enquiries
                    ({cmp.change > 0 ? "+" : ""}{cmp.change}{cmp.change_pct != null ? `, ${cmp.change_pct > 0 ? "+" : ""}${cmp.change_pct}%` : ""}){cmp.small_sample ? " — small sample" : ""}.</p>
                )}
              </section>

              <section className="trends-section trends-ai">
                <h3>AI-Generated Insights <span className="trends-ai-badge">AI</span></h3>
                <p className="trends-hint">Written by AI from the verified figures above. The figures themselves are not AI-generated.</p>
                {insights.status === "loading" && <p className="trends-state" role="status">Preparing analysis…</p>}
                {insights.status === "unavailable" && <p className="trends-state" role="status">{insights.message} The charts and figures above are unaffected.</p>}
                {insights.status === "ready" && (
                  <dl>
                    {INSIGHT_LABELS.map(([key, label]) => (
                      <div key={key}><dt>{label}</dt>
                        <dd>{Array.isArray(insights.data[key]) ? <ul>{insights.data[key].map((line) => <li key={line}>{line}</li>)}</ul> : insights.data[key]}</dd></div>
                    ))}
                  </dl>
                )}
              </section>
              <p className="trends-foot">Report generated {formatGeneratedAt(report.generated_at)} · {reportFileName(report)}</p>
            </>
          )}
        </div>
      {detail && report && (
        <TrendDetailModal report={report} detail={detail} onClose={() => setDetail(null)}
          loadQuestions={(filters) => getCaseTypeTrendQuestions(period, anchor, customRange, filters)} />
      )}
    </section>
  );
}
