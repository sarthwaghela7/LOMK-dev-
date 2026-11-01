import { useEffect, useRef, useState } from "react";

const optionValue = (option) => (typeof option === "string" ? option : option.value);
const optionLabel = (option) => (typeof option === "string" ? option : option.label);
const chosenValues = (value) => (Array.isArray(value) ? value : value ? [value] : []);
const isActive = (value) => (Array.isArray(value) ? value.length > 0 : Boolean(value));

// A dropdown with a checkbox per value, for filters that take several values.
// Nothing ticked means "All".
function MultiSelectFilter({ label, options, value, onChange }) {
  const [open, setOpen] = useState(false);
  const root = useRef(null);
  const selected = chosenValues(value);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => {
      if (event.type === "keydown" ? event.key === "Escape" : !root.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  const toggle = (option) =>
    onChange(selected.includes(option) ? selected.filter((item) => item !== option) : [...selected, option]);
  const summary = !selected.length
    ? `All ${label}`
    : selected.length <= 2
      ? selected.join(", ")
      : `${selected.length} selected`;

  return (
    <div className="calls-filter-group data-filter-field data-filter-multi" ref={root}>
      <span className="calls-toolbar-label">{label}</span>
      <button
        type="button"
        className="calls-filter-select data-filter-multi-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="data-filter-multi-summary" title={summary}>{summary}</span>
        <i className="fa-solid fa-chevron-down" aria-hidden="true" />
      </button>
      {open && (
        <div className="data-filter-multi-menu" role="listbox" aria-multiselectable="true" aria-label={label}>
          <label className="data-filter-multi-option">
            <input type="checkbox" checked={!selected.length} onChange={() => onChange([])} />
            <span>All {label}</span>
          </label>
          {options.map((option) => (
            <label key={optionValue(option)} className="data-filter-multi-option">
              <input
                type="checkbox"
                checked={selected.includes(optionValue(option))}
                onChange={() => toggle(optionValue(option))}
              />
              <span>{optionLabel(option)}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

export function DataFilterPanel({ filters, values, onChange, onClear }) {
  const activeCount = filters.filter((filter) => isActive(values[filter.key])).length;

  return (
    <section className="data-filter-panel" aria-label="Filter table data">
      <div className="data-filter-panel-heading">
        <div>
          <strong>Filter Data</strong>
          <span>Select one or more values. Results update immediately.</span>
        </div>
        {activeCount > 0 && <button type="button" className="table-action" onClick={onClear}>Clear all ({activeCount})</button>}
      </div>
      <div className="data-filter-grid">
        {filters.map((filter) => filter.multiple ? (
          <MultiSelectFilter
            key={filter.key}
            label={filter.label}
            options={filter.options}
            value={values[filter.key]}
            onChange={(next) => onChange(filter.key, next)}
          />
        ) : (
          <label key={filter.key} className="calls-filter-group data-filter-field">
            <span className="calls-toolbar-label">{filter.label}</span>
            <select
              className="calls-filter-select"
              value={values[filter.key] || ""}
              onChange={(event) => onChange(filter.key, event.target.value)}
            >
              <option value="">All {filter.label}</option>
              {filter.options.map((option) => (typeof option === "string"
                ? <option key={option} value={option}>{option}</option>
                : <option key={option.value} value={option.value}>{option.label}</option>))}
            </select>
          </label>
        ))}
      </div>
    </section>
  );
}
