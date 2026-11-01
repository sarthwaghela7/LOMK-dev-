import { saveStatusBadge } from "../data/submitState.js";

/**
 * Compact save/submit status of one Calls row, shown in the frozen columns so it stays visible while the table is
 * scrolled sideways. Pure presentation: it is derived from the same state as the controls in the last column
 * (see saveStatusBadge), and it has no timer - it stays until the state really changes.
 */
export default function CallSaveStatus({ state, autosave, completed }) {
  const badge = saveStatusBadge(state, autosave, { completed });
  return (
    <div className="calls-save-status" role="status" aria-live="polite">
      <span className={`calls-save-notice calls-save-notice-${badge.tone} calls-save-status-badge`} title={badge.title || undefined}>
        {badge.label}
      </span>
      {badge.completed && <span className="calls-save-notice calls-save-notice-completed calls-save-status-badge">Completed</span>}
    </div>
  );
}
