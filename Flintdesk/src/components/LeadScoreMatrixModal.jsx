const LEAD_SCORE_CRITERIA = [
  ["Urgency signal", "25", "None mentioned", "Vague concern about timing", "Client Distressed, RFE, NOID, Court Date, Filing Deadline, Grace Period, Visa Expiry"],
  ["Case complexity", "20", 'General "which visa is right for me"', "F-1/OPT, B-1/B-2, N-400, standard Green Card", "PERM, EB-1/EB1C, EB-2 NIW, L-1, I-485 AOS, multi-petition family case"],
  ["Employer involvement", "15", "Individual only, no employer", "Employer aware, not on the call", "Employer/HR sponsoring or on the call"],
  ["Lead source trust", "15", "Cold Ads/Brevo click", "Google Search, YouTube, Friends, Instagram", "Client Referral or repeat Paid Consultation Client"],
  ["Existing relationship", "10", "New, no prior contact", "New, referred by current client", "Existing client of the firm"],
  ["Readiness / decision stage", "15", "Just researching, no documents, no timeline", "Some documents, asking process questions", 'Has deadline, documents in hand, asking "what do I do next"'],
];

const LEAD_SCORE_ACTIONS = [
  ["70–100", "BOOK NOW", "Book a paid consultation immediately — offer next available attorney slot on this call."],
  ["40–69", "GATHER MORE INFO", "Get more detail (case type, employer, deadline) this call or on a scheduled follow-up, then re-score."],
  ["0–39", "NURTURE", "Give general front-desk info, log in tracker, route to nurture / follow-up sequence."],
];

export function LeadScoreMatrixModal({ onClose }) {
  return (
    <div className="call-modal-overlay lead-score-matrix-overlay" role="presentation">
      <section className="call-modal-card lead-score-matrix-modal" role="dialog" aria-modal="true" aria-labelledby="lead-score-matrix-title">
        <header className="lead-score-matrix-header">
          <div>
            <h2 id="lead-score-matrix-title">PAID CONSULTATION — LEAD SCORING RUBRIC</h2>
            <p>Law Offices of Mary Kennedy, LLC | CRO Front Desk Quick Reference</p>
          </div>
          <button type="button" className="call-modal-close" onClick={onClose}>Close</button>
        </header>
        <div className="lead-score-matrix-content">
          <table className="lead-score-matrix-table">
            <thead><tr><th>Criteria</th><th>Max</th><th>0 pts</th><th>5–10 pts</th><th>15–25 pts</th></tr></thead>
            <tbody>{LEAD_SCORE_CRITERIA.map((row) => <tr key={row[0]}>{row.map((value, index) => <td key={index}>{value}</td>)}</tr>)}</tbody>
          </table>
          <table className="lead-score-matrix-table lead-score-actions-table">
            <thead><tr><th>Score</th><th>Action</th><th>What to do</th></tr></thead>
            <tbody>{LEAD_SCORE_ACTIONS.map((row) => <tr key={row[0]}>{row.map((value, index) => <td key={index}>{value}</td>)}</tr>)}</tbody>
          </table>
          <p className="lead-score-override"><strong>OVERRIDE RULE:</strong> Any urgency flag (NOID, RFE, Court Date, Client Distressed) qualifies for a same-day consult offer immediately, regardless of total score.</p>
        </div>
      </section>
    </div>
  );
}
