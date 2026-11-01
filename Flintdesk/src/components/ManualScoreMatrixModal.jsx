import { MANUAL_SCORE_BANDS } from "../data/manualScoreBands.js";

export function ManualScoreMatrixModal({ onClose }) {
  return (
    <div className="call-modal-overlay lead-score-matrix-overlay" role="presentation">
      <section className="call-modal-card lead-score-matrix-modal" role="dialog" aria-modal="true" aria-labelledby="manual-score-matrix-title">
        <header className="lead-score-matrix-header">
          <div>
            <h2 id="manual-score-matrix-title">MANUAL DATA SCORE — QUALITY RUBRIC</h2>
            <p>How Stage 2–4 manual entries are graded, 0–10</p>
          </div>
          <button type="button" className="call-modal-close" onClick={onClose}>Close</button>
        </header>
        <div className="lead-score-matrix-content">
          <table className="lead-score-matrix-table">
            <thead>
              <tr>
                <th>Score</th>
                <th>Rating</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {MANUAL_SCORE_BANDS.map((band) => (
                <tr key={band.key}>
                  <td>{band.range}</td>
                  <td>{band.label}</td>
                  <td>{band.blurb}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
