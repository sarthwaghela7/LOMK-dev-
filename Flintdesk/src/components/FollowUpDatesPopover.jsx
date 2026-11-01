import { useCallback, useEffect, useId, useRef, useState } from "react";
import { getFollowUpDates } from "../data/api.js";
import { cleanFollowUpDates, formatFollowUpDate } from "../data/followUpDates.js";

const FILTER_DEBOUNCE_MS = 350;

/**
 * "Follow Up Dates" button + small left-anchored popover for the Calls page.
 *
 * - `filters`: the filters currently shown in the filter belt (including values typed or chosen
 *   but not yet applied). The popover list follows them live, so it always reflects the belt.
 * - `appliedFilters`: the filters the table is actually using. They are only used to check that
 *   an already-selected date is still valid; the table is never changed by unapplied belt edits.
 * - Picking a date calls `onSelect("YYYY-MM-DD")`; the page applies the belt together with that
 *   date. A selected date that is no longer available under the applied filters is cleared.
 */
export default function FollowUpDatesPopover({ filters, appliedFilters, selected, onSelect, onClear }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState({ status: "idle", dates: [], error: "" });
  const root = useRef(null);
  const button = useRef(null);
  const listRequest = useRef(0);
  const checkRequest = useRef(0);
  const loadedKey = useRef(null);
  const popoverId = useId();
  const filtersKey = JSON.stringify(filters);
  const appliedKey = JSON.stringify(appliedFilters || filters);

  const load = useCallback((key) => {
    const id = ++listRequest.current; // an older response must never overwrite a newer one
    loadedKey.current = key;
    setState({ status: "loading", dates: [], error: "" }); // never show a previous list while loading
    getFollowUpDates(JSON.parse(key))
      .then((dates) => {
        if (id === listRequest.current) setState({ status: "ready", dates: cleanFollowUpDates(dates), error: "" });
      })
      .catch((error) => {
        if (id === listRequest.current) setState({ status: "error", dates: [], error: error?.message || "Unable to load follow-up dates." });
      });
  }, []);

  // While open, the list follows the filter belt: immediately on open, debounced while filters change.
  useEffect(() => {
    if (!open) {
      listRequest.current += 1;
      loadedKey.current = null;
      setState((current) => (current.status === "idle" ? current : { status: "idle", dates: [], error: "" }));
      return undefined;
    }
    if (loadedKey.current === filtersKey) return undefined;
    const timer = setTimeout(() => load(filtersKey), loadedKey.current === null ? 0 : FILTER_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [open, filtersKey, load]);

  // The selected date must stay valid for the filters the table really uses; otherwise it is dropped.
  useEffect(() => {
    if (!selected) return undefined;
    const id = ++checkRequest.current;
    getFollowUpDates(JSON.parse(appliedKey))
      .then((dates) => {
        if (id === checkRequest.current && !cleanFollowUpDates(dates).includes(selected)) onClear();
      })
      .catch(() => { /* keep the selection if the check itself fails */ });
    return () => { checkRequest.current += 1; };
  }, [selected, appliedKey, onClear]);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event) => { if (!root.current?.contains(event.target)) setOpen(false); };
    const onKeyDown = (event) => {
      if (event.key === "Escape") { setOpen(false); button.current?.focus(); }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const choose = (iso) => { setOpen(false); onSelect(iso); };
  const moveFocus = (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const items = [...root.current.querySelectorAll("[data-follow-up-item]")];
    const index = items.indexOf(document.activeElement);
    const next = event.key === "ArrowDown" ? Math.min(index + 1, items.length - 1) : Math.max(index - 1, 0);
    items[next]?.focus();
    event.preventDefault();
  };

  return (
    <div className="follow-up-dates" ref={root}>
      <button
        ref={button}
        type="button"
        className="table-action follow-up-dates-button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? popoverId : undefined}
        onClick={() => setOpen((value) => !value)}
      >
        <i className="fa-solid fa-arrow-right" aria-hidden="true" /> Follow Up Dates
      </button>
      {selected && (
        <span className="follow-up-dates-chip">
          Follow-up: {formatFollowUpDate(selected)}
          <button type="button" aria-label="Clear follow-up date filter" onClick={onClear}>×</button>
        </span>
      )}
      {open && (
        <div id={popoverId} className="follow-up-dates-popover" role="dialog" aria-label="Follow Up Dates" onKeyDown={moveFocus}>
          <strong>Follow Up Dates</strong>
          {(state.status === "loading" || state.status === "idle") && <p className="follow-up-dates-note" role="status">Loading dates...</p>}
          {state.status === "error" && (
            <p className="follow-up-dates-note follow-up-dates-error" role="alert">
              {state.error} <button type="button" data-follow-up-item onClick={() => load(filtersKey)}>Retry</button>
            </p>
          )}
          {state.status === "ready" && !state.dates.length && (
            <p className="follow-up-dates-note">No follow-up dates in this date range</p>
          )}
          {state.status === "ready" && state.dates.length > 0 && (
            <ul>
              {state.dates.map((iso) => (
                <li key={iso}>
                  <button
                    type="button"
                    data-follow-up-item
                    className={iso === selected ? "selected" : ""}
                    aria-current={iso === selected ? "true" : undefined}
                    onClick={() => choose(iso)}
                  >
                    {formatFollowUpDate(iso)}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {selected && <button type="button" className="follow-up-dates-clear" onClick={() => { setOpen(false); onClear(); }}>Clear selection</button>}
        </div>
      )}
    </div>
  );
}
