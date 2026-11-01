import { useCallback, useEffect, useRef, useState } from "react";
import { formatShortDate, periodRangeText } from "../data/caseTypeTrends.js";

// Click-through popup for the Trends view: the actual enquiry questions behind the clicked figure, one per call,
// fetched on demand for the same period the report shows. "Questions" = enquiry questions recorded on calls:
// one per enquiry call (not calls in general, not clients). The count shown is the number of questions listed.
export default function TrendDetailModal({ report, detail, onClose, loadQuestions }) {
  const closeRef = useRef(null);
  const [state, setState] = useState({ status: "loading", data: null, error: "" });
  useEffect(() => { closeRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (event) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const filters = detail.kind === "caseType" ? { caseType: detail.name } : { question: detail.text };
  const filtersKey = JSON.stringify(filters);
  const load = useCallback(() => {
    let current = true;
    setState({ status: "loading", data: null, error: "" });
    loadQuestions(JSON.parse(filtersKey))
      .then((data) => { if (current) setState({ status: "ready", data, error: "" }); })
      .catch((failure) => { if (current) setState({ status: "error", data: null, error: failure?.message || "Unable to load the questions." }); });
    return () => { current = false; };
  }, [filtersKey, loadQuestions]);       // eslint-disable-line react-hooks/exhaustive-deps -- loadQuestions is recreated every render
  useEffect(() => load(), [filtersKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const range = periodRangeText(report.period);
  const items = state.data?.items || [];
  const total = state.data?.total ?? 0;
  const expected = detail.kind === "caseType" ? report.case_types.find((c) => c.name === detail.name)?.count
    : (report.repeated_questions.find((q) => q.text === detail.text) || report.questions.find((q) => q.text === detail.text))?.count;
  const extra = detail.kind === "caseType" ? report.by_case_type.find((c) => c.name === detail.name) : null;
  const title = detail.kind === "caseType" ? detail.name : "Most asked query";

  return (
    <div className="call-modal-overlay trends-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="trends-detail-modal" role="dialog" aria-modal="true" aria-labelledby="trends-detail-title">
        <header className="trends-header">
          <div>
            <h2 id="trends-detail-title">{title}</h2>
            <p className="trends-sub">{range} · Central Time</p>
          </div>
          <button type="button" ref={closeRef} className="call-modal-close" onClick={onClose} aria-label="Close details">×</button>
        </header>
        <div className="trends-detail-body">
          {detail.kind === "question" && <p className="trends-detail-question">{detail.text}</p>}
          {state.status === "loading" && <p className="trends-state" role="status">Loading questions…</p>}
          {state.status === "error" && <p className="trends-state trends-error" role="alert">{state.error} <button type="button" className="calls-save-retry" onClick={load}>Retry</button></p>}
          {state.status === "ready" && (
            <>
              <p className="trends-detail-figure"><strong>{total}</strong> {detail.kind === "question" ? (total === 1 ? "time asked" : "times asked") : (total === 1 ? "question" : "questions")}</p>
              {extra && <p className="trends-hint">{extra.distinct_questions} distinct · {extra.repeated_questions} asked more than once.</p>}
              {expected !== undefined && expected !== total && <p className="trends-hint">Figures updated while this was open ({expected} when the page loaded). They refresh automatically.</p>}
              {state.data.truncated && <p className="trends-hint">Showing the first {items.length} of {total} questions.</p>}
              {total === 0 ? <p className="trends-empty">No questions were recorded for this selection in this period.</p> : (
                <ol className="trends-detail-list">
                  {items.map((item, index) => (
                    <li key={`${item.call_ref}-${item.date}-${index}`}>
                      <span className="trends-detail-meta">{formatShortDate(item.date)}{item.call_ref ? ` · ${item.call_ref}` : ""}{detail.kind === "question" ? ` · ${item.case_type}` : ""}</span>
                      <span className="trends-detail-text">{item.question}</span>
                    </li>
                  ))}
                </ol>
              )}
            </>
          )}
        </div>
        <footer className="trends-detail-foot"><button type="button" className="table-action" onClick={onClose}>Close</button></footer>
      </section>
    </div>
  );
}
