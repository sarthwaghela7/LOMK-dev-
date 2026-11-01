import { useRef } from "react";
import { formatDisplayDate } from "../data/displayDate.js";

function parseDateInput(value) {
  const text = String(value || "").trim();
  const match = text.match(
    /^(\d{4})-(\d{2})-(\d{2})$|^(\d{2})\/(\d{2})\/(\d{4})$/,
  );
  if (!match) return "";
  const [, yearFirst, monthFirst, dayFirst, month, day, year] = match;
  const normalized = yearFirst
    ? `${yearFirst}-${monthFirst}-${dayFirst}`
    : `${year}-${month}-${day}`;
  const [y, m, d] = normalized.split("-").map(Number);
  // Build the date from local calendar components (not an ISO string) and
  // round-trip through toISOString(), which reinterprets it in UTC, would
  // shift the day for any user east of UTC (e.g. IST) and silently reject
  // every typed date.
  const date = new Date(y, m - 1, d);
  const isValid =
    date.getFullYear() === y && date.getMonth() === m - 1 && date.getDate() === d;
  return isValid ? normalized : "";
}

export function DateRangeSelector({ value, onChange, compact = false }) {
  const fromRef = useRef(null);
  const toRef = useRef(null);
  const openPicker = (ref) => {
    if (ref.current?.showPicker) ref.current.showPicker();
    else ref.current?.focus();
  };
  const renderDateInput = (ref, label, dateValue, constraint, onDateChange) => (
    <label className="date-input">
      <span className="sr-only">{label}</span>
      <input
        className="date-display-input"
        type="text"
        aria-label={label}
        value={formatDisplayDate(dateValue, "")}
        onChange={(event) => {
          const parsed = parseDateInput(event.target.value);
          if (parsed) onDateChange({ target: { value: parsed } });
        }}
        onBlur={(event) => {
          const parsed = parseDateInput(event.target.value);
          if (parsed) onDateChange({ target: { value: parsed } });
        }}
        onClick={() => openPicker(ref)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            const parsed = parseDateInput(event.currentTarget.value);
            if (parsed) onDateChange({ target: { value: parsed } });
            else openPicker(ref);
          }
          if (event.key === " ") openPicker(ref);
        }}
      />
      <input
        ref={ref}
        className="date-picker-input"
        type="date"
        tabIndex={-1}
        aria-hidden="true"
        value={dateValue}
        {...constraint}
        onChange={onDateChange}
      />
    </label>
  );

  return (
    <div
      className={`calls-filter-group calls-date-group${compact ? " calls-date-group-compact" : ""}`}
    >
      <span className="calls-toolbar-label">Date range</span>
      <div className="calls-toolbar calls-date-toolbar">
        {renderDateInput(
          fromRef,
          "Date From",
          value.start,
          { max: value.end || undefined },
          (event) => onChange({ ...value, start: event.target.value }),
        )}
        <span className="calls-toolbar-label">to</span>
        {renderDateInput(
          toRef,
          "Date To",
          value.end,
          { min: value.start || undefined },
          (event) => onChange({ ...value, end: event.target.value }),
        )}
      </div>
    </div>
  );
}
