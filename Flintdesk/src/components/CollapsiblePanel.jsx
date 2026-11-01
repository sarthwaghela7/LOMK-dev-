import { useId, useState } from "react";

const STORAGE_PREFIX = "flintdesk_panel_";

function readStored(id, fallback) {
  if (!id) return fallback;
  try {
    const value = localStorage.getItem(STORAGE_PREFIX + id);
    return value === null ? fallback : value === "1";
  } catch {
    return fallback;
  }
}

/**
 * Shared collapsible wrapper for instruction / "important note" / help panels.
 * Collapsed by default so these panels cost one slim row of vertical space.
 * Pass a stable `id` to remember the open/closed state between visits.
 */
export function CollapsiblePanel({
  id,
  title,
  icon = "fa-circle-info",
  tone = "info",
  defaultOpen = false,
  className = "",
  children,
}) {
  const [open, setOpen] = useState(() => readStored(id, defaultOpen));
  const bodyId = useId();

  const toggle = () => {
    setOpen((current) => {
      const next = !current;
      if (id) {
        try {
          localStorage.setItem(STORAGE_PREFIX + id, next ? "1" : "0");
        } catch {
          /* state is a convenience only */
        }
      }
      return next;
    });
  };

  return (
    <aside className={`collapsible-panel tone-${tone} ${open ? "is-open" : ""} ${className}`.trim()}>
      <button
        type="button"
        className="collapsible-panel-toggle"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={toggle}
      >
        <i className="fa-solid fa-chevron-right collapsible-panel-chevron" aria-hidden="true" />
        {icon && <i className={`fa-solid ${icon} collapsible-panel-icon`} aria-hidden="true" />}
        <span className="collapsible-panel-title">{title}</span>
      </button>
      <div id={bodyId} className="collapsible-panel-body" role="region" aria-label={title} inert={!open}>
        <div className="collapsible-panel-inner">{children}</div>
      </div>
    </aside>
  );
}
