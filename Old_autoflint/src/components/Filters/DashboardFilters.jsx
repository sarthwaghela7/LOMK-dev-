import { CalendarRange, User, BarChart3, RotateCcw } from "lucide-react";

function DashboardFilters({ filters, onChange, agents, onReset, onApply }) {
  const handleChange = (key) => (e) => {
    onChange({ [key]: e.target.value });
  };

  return (
    <div className="dashboard-filters-bar">
      <div className="dashboard-filters-row">
        {/* Date Range */}
        <div className="dashboard-filter-group">
          <CalendarRange size={14} />
          <label>From</label>
          <input
            type="date"
            lang="en-CA"
            value={filters.startDate}
            onChange={handleChange("startDate")}
            className="dashboard-filter-input"
          />
        </div>
        <div className="dashboard-filter-group">
          <label>To</label>
          <input
            type="date"
            lang="en-CA"
            value={filters.endDate}
            onChange={handleChange("endDate")}
            className="dashboard-filter-input"
          />
        </div>

        {/* Agent Dropdown — all four reps always listed, plain names */}
        <div className="dashboard-filter-group">
          <User size={14} />
          <label>Agent</label>
          <select
            value={filters.agent}
            onChange={handleChange("agent")}
            className="dashboard-filter-select"
          >
            <option value="">All Agents</option>
            {agents.map((agent) => (
              <option key={agent} value={agent}>
                {agent}
              </option>
            ))}
          </select>
        </div>

        {/* Quality Score Range */}
        <div className="dashboard-filter-group quality-range">
          <BarChart3 size={14} />
          <label>Quality Score</label>
          <div className="quality-inputs">
            <input
              type="number"
              min="0"
              max="10"
              step="0.1"
              placeholder="Min"
              value={filters.minQualityScore}
              onChange={handleChange("minQualityScore")}
              className="dashboard-filter-input score-input"
            />
            <span className="quality-separator">—</span>
            <input
              type="number"
              min="0"
              max="10"
              step="0.1"
              placeholder="Max"
              value={filters.maxQualityScore}
              onChange={handleChange("maxQualityScore")}
              className="dashboard-filter-input score-input"
            />
          </div>
        </div>

        {/* Actions */}
        <div className="dashboard-filter-actions">
          <button
            type="button"
            className="dashboard-filter-btn btn-reset"
            onClick={onReset}
            title="Reset to current month"
          >
            <RotateCcw size={14} />
            Reset
          </button>
          <button
            type="button"
            className="dashboard-filter-btn btn-apply"
            onClick={onApply}
          >
            Apply Filters
          </button>
        </div>
      </div>
    </div>
  );
}

export default DashboardFilters;