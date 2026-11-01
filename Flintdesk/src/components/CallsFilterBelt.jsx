import { useEffect, useId, useRef, useState } from "react";
import { usePersistentTableState } from "../data/persistentTableState.js";
import {
  ACTION_STATUS_OPTIONS, LIFECYCLE_OPTIONS, LOG_UPDATE_OPTIONS, PRIORITY_OPTIONS, activeBeltFilterCount, openCaseOptions, sameBeltSelection,
} from "../data/callsFilterBelt.js";

// The Calls page's static filter belt: one dropdown per filter category. Choosing a value only changes the draft; the page's
// Apply button turns the drafts into the applied filters (and so reloads the table). A dropdown that holds a value is
// highlighted, and the heading counts how many filters are applied right now.
function BeltSelect({ id, label, value, options, emptyLabel, onChange, applied }) {
  const active = Boolean(value) && value !== "all";
  return (
    <label className={`calls-belt-field ${active ? "is-active" : ""} ${active && value !== applied ? "is-pending" : ""}`}>
      <span className="calls-belt-label">{label}</span>
      <select
        id={id}
        className="calls-filter-select"
        value={active ? value : ""}
        onChange={(event) => onChange(event.target.value || (id === "belt-log-updates" ? "all" : ""))}
        aria-label={label}
      >
        <option value="">{emptyLabel}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value} disabled={option.disabled}>{option.label}</option>
        ))}
      </select>
    </label>
  );
}

export default function CallsFilterBelt({ draft, applied, agentName, onChange, onClear, loading, followUpControl, showPriority = true }) {
  const appliedCount = activeBeltFilterCount(applied);
  const draftCount = activeBeltFilterCount(draft);
  const unapplied = !sameBeltSelection(draft, applied);
  // Collapsing only hides the controls: they stay mounted, so every selection (applied or not) is kept and nothing is reapplied.
  const [collapsed, setCollapsed] = usePersistentTableState("Calls.filtersCollapsed", false);
  const bodyId = useId();
  // While the panel animates open or closed its content is clipped; once it has settled the clip is removed again so the
  // Follow Up Dates popover can extend past the panel.
  const [animating, setAnimating] = useState(false);
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) { firstRender.current = false; return undefined; }
    setAnimating(true);
    const timer = window.setTimeout(() => setAnimating(false), 260);
    return () => window.clearTimeout(timer);
  }, [collapsed]);
  return (
    <section className={`calls-belt-panel ${collapsed ? "is-collapsed" : ""}`} role="group" aria-label="Call record filters">
      <div className="calls-belt-heading">
        <button type="button" className="calls-belt-toggle" aria-expanded={!collapsed} aria-controls={bodyId}
          aria-label={collapsed ? "Expand filters" : "Collapse filters"} title={collapsed ? "Show filters" : "Hide filters"}
          onClick={() => setCollapsed((current) => !current)}>
          <i className="fa-solid fa-chevron-down calls-belt-chevron" aria-hidden="true" />
          <strong>Filters</strong>
        </button>
        <span className={`calls-belt-count ${appliedCount ? "on" : ""}`} role="status">
          {appliedCount ? `${appliedCount} filter${appliedCount === 1 ? "" : "s"} applied` : "No filters applied"}
        </span>
        {unapplied && <span className="calls-belt-pending">Changes not applied yet - click Apply</span>}
      </div>
      <div id={bodyId} className={`calls-belt-collapsible ${collapsed ? "is-collapsed" : ""} ${animating ? "is-animating" : ""}`}>
      <div className="calls-belt-fields" inert={collapsed} aria-hidden={collapsed ? "true" : undefined}>
        <BeltSelect id="belt-lifecycle" label="Case Lifecycle" value={draft.lifecycleStage} applied={applied.lifecycleStage}
          options={LIFECYCLE_OPTIONS} emptyLabel="All cases" onChange={onChange.lifecycleStage} />
        <BeltSelect id="belt-open-cases" label="Open Cases" value={draft.openCase} applied={applied.openCase}
          options={openCaseOptions(agentName)} emptyLabel="Any" onChange={onChange.openCase} />
        {showPriority && (
          <BeltSelect id="belt-priority" label="Priority Cases" value={draft.priorityForm} applied={applied.priorityForm}
            options={PRIORITY_OPTIONS} emptyLabel="Any" onChange={onChange.priorityForm} />
        )}
        <BeltSelect id="belt-action-status" label="Action Status" value={draft.actionStatus} applied={applied.actionStatus}
          options={ACTION_STATUS_OPTIONS} emptyLabel="Any" onChange={onChange.actionStatus} />
        <BeltSelect id="belt-log-updates" label="Log Updates" value={draft.logUpdate} applied={applied.logUpdate}
          options={LOG_UPDATE_OPTIONS} emptyLabel="All" onChange={onChange.logUpdate} />
        <div className={`calls-belt-field calls-belt-followups ${draft.followUpDate ? "is-active" : ""}`}>
          <span className="calls-belt-label">Follow Up Dates</span>
          {followUpControl}
        </div>
        <button type="button" className="table-action calls-belt-clear" onClick={onClear} disabled={loading || (!appliedCount && !draftCount)}>
          Clear filters
        </button>
      </div>
      </div>
    </section>
  );
}
