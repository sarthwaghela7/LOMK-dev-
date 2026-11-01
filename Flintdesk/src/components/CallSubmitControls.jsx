/**
 * Save / Submit controls of one Calls row: the Submit button plus the small status notices.
 * Pure presentation - the state comes from data/submitState.js (deriveSubmitState).
 *
 * `autosave` is the row's last autosave outcome: { phase: "idle" | "saving" | "saved" | "error", message, retry }.
 */
export default function CallSubmitControls({ state, autosave, onSubmit }) {
  const phase = autosave?.phase || "idle";
  const showSaving = phase === "saving" && state.kind !== "saving";
  return (
    <div className="calls-submit-controls">
      <div className="calls-save-notices" aria-live="polite">
        {state.badge && (
          <span className={`calls-save-notice calls-save-notice-${state.tone}`} title={state.error || undefined}>
            {state.badge}
          </span>
        )}
        {showSaving && <span className="calls-save-notice calls-save-notice-busy">Saving…</span>}
        {phase === "saved" && !state.badge && <span className="calls-save-notice calls-save-notice-ok">Saved ✓</span>}
        {phase === "error" && (
          <span className="calls-save-notice calls-save-notice-error" role="alert">
            Save failed{autosave.message ? `: ${autosave.message}` : ""}
            {autosave.retry && (
              <button type="button" className="calls-save-retry" onClick={autosave.retry}>Retry</button>
            )}
          </span>
        )}
      </div>
      <button
        type="button"
        title={state.title}
        className={`primary-button leads-row-action${state.tone === "ok" ? " completed-button" : ""} calls-submit-${state.kind}`}
        disabled={state.disabled}
        onClick={onSubmit}
      >
        {state.label}
      </button>
    </div>
  );
}
