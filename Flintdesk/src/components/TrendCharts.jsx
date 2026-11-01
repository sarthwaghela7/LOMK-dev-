import { barPercent, labelEvery } from "../data/caseTypeTrends.js";

/**
 * Ranked horizontal bars (case types, questions). Plain HTML/CSS so long labels wrap instead of being cut off.
 * items: [{ label, value, sub?, muted?, highlight?, title? }]
 */
export function RankedBars({ items, max, unit = "", emptyText = "No data" }) {
  if (!items.length) return <p className="trends-empty">{emptyText}</p>;
  const top = max ?? Math.max(...items.map((item) => item.value));
  return (
    <ol className="trends-bars">
      {items.map((item) => (
        <li key={item.label} className={`${item.muted ? "muted" : ""} ${item.highlight ? "leading" : ""} ${item.onSelect ? "clickable" : ""}`} title={item.title || item.label}
          {...(item.onSelect ? { role: "button", tabIndex: 0, onClick: item.onSelect,
            onKeyDown: (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); item.onSelect(); } } } : {})}>
          <span className="trends-bar-label">{item.label}{item.sub && <small>{item.sub}</small>}</span>
          <span className="trends-bar-track"><span className="trends-bar-fill" style={{ width: `${barPercent(item.value, top)}%` }} /></span>
          <span className="trends-bar-value">{item.value}{unit}</span>
        </li>
      ))}
    </ol>
  );
}

/**
 * Column chart over time. points: [{ label, value, hatched? }]. `hatched` marks a day with no calls recorded
 * (not the same as zero enquiries). Future days are simply not passed in, so nothing is drawn for them.
 */
export function ColumnChart({ points, ariaLabel, yLabel = "Enquiries" }) {
  const width = 640, height = 220, left = 38, bottom = 36, top = 14;
  if (!points.length) return <p className="trends-empty">No data</p>;
  const max = Math.max(1, ...points.map((p) => p.value));
  const niceMax = max <= 4 ? max : Math.ceil(max / 4) * 4;
  const plotW = width - left - 8, plotH = height - top - bottom;
  const slot = plotW / points.length, barW = Math.min(34, slot * 0.7);
  const every = labelEvery(points.length);
  const ticks = [0, 0.5, 1].map((f) => Math.round(niceMax * f));
  return (
    <svg className="trends-columns" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
      <defs>
        <pattern id="trends-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="6" height="6" fill="#f1efe9" /><line x1="0" y1="0" x2="0" y2="6" stroke="#c9c4b8" strokeWidth="2" />
        </pattern>
      </defs>
      <text x="4" y={top + 4} className="trends-axis-title">{yLabel}</text>
      {ticks.map((tick, index) => {
        const y = top + plotH - (tick / niceMax) * plotH;
        return <g key={`${tick}-${index}`}><line x1={left} x2={width - 8} y1={y} y2={y} className="trends-grid" /><text x={left - 6} y={y + 3} textAnchor="end" className="trends-tick">{tick}</text></g>;
      })}
      {points.map((point, index) => {
        const h = (point.value / niceMax) * plotH;
        const x = left + index * slot + (slot - barW) / 2;
        const y = top + plotH - h;
        return (
          <g key={`${point.label}-${index}`}>
            {point.hatched
              ? <rect x={x} y={top + plotH - 10} width={barW} height={10} fill="url(#trends-hatch)" stroke="#c9c4b8"><title>{point.label}: no calls recorded</title></rect>
              : <rect x={x} y={y} width={barW} height={Math.max(h, point.value > 0 ? 2 : 0)} className="trends-column" rx="2"><title>{point.label}: {point.value}</title></rect>}
            {!point.hatched && point.value > 0 && <text x={x + barW / 2} y={y - 4} textAnchor="middle" className="trends-value">{point.value}</text>}
            {index % every === 0 && <text x={x + barW / 2} y={height - bottom + 14} textAnchor="middle" className="trends-tick">{point.label}</text>}
          </g>
        );
      })}
      <line x1={left} x2={width - 8} y1={top + plotH} y2={top + plotH} className="trends-axis" />
    </svg>
  );
}
