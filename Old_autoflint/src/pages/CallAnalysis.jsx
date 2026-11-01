import { useEffect, useMemo, useState } from "react";
import * as XLSX from "xlsx";
import { FileSpreadsheet, Maximize2, Minimize2 } from "lucide-react";

import { BufferingOverlay } from "../components/BufferingLoader";
import { getCallAnalysis, getPrompts } from "../data/api";

const LS_FILTERS = "call_analysis_filters";
const LS_DATA = "call_analysis_data";
const LS_PROMPTS = "call_analysis_prompts";
const DEFAULT_START_DATE = "2000-01-01";

const EXCEL_COLORS = {
  titleBg: "18212F",
  titleText: "F8FAFC",
  subtitleText: "CBD5E1",
  headerBg: "2563EB",
  headerText: "FFFFFF",
  border: "334155",
  rowAlt: "0F172A",
  rowBase: "111827",
  greenBg: "064E3B",
  greenText: "86EFAC",
  redBg: "7F1D1D",
  redText: "FCA5A5",
  yellowBg: "713F12",
  yellowText: "FCD34D",
  neutralBg: "1E293B",
  neutralText: "E2E8F0",
};

// Only these Action/Follow-up dropdown fields get conditional color-coding
// in the Excel export. Every other field exports as plain/normal text so
// the sheet isn't wall-to-wall colored badges.
const ACTION_FOLLOWUP_FIELD_KEYS = new Set([
  "follow_up_status",
  "follow_up_required",
  "escalation_required",
  "client_response_to_consultation",
]);

function loadSaved(key, fallback) {
  try {
    const saved = localStorage.getItem(key);
    if (saved) return JSON.parse(saved);
  } catch {
    return fallback;
  }
  return fallback;
}

function formatCellValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function parseMaybeJson(value) {
  if (!value || typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function getFieldValue(row, key) {
  if (!row) return undefined;
  if (row[key] !== undefined && row[key] !== null && row[key] !== "") {
    return row[key];
  }
  const raw = parseMaybeJson(row.raw_llm_json);
  if (raw && typeof raw === "object" && raw[key] !== undefined && raw[key] !== null && raw[key] !== "") {
    return raw[key];
  }
  return undefined;
}

function normalizePromptField(prompt) {
  const key = (prompt?.key || "").trim();
  if (!key || prompt?.id === 1) return null;
  return {
    id: prompt.id,
    dbKey: key,
    name: key,
    notes: (prompt?.notes || "").trim(),
  };
}

function deriveFallbackFields(rows) {
  const excluded = new Set([
    "id",
    "raw_llm_json",
    "model_name",
    "processing_status",
    "created_at",
    "call_id",
    "call_number",
    "call_reference",
    "formatted_call_id",
  ]);
  const seen = new Set();
  const keys = [];
  for (const row of rows) {
    const raw = parseMaybeJson(row.raw_llm_json);
    const source = raw && typeof raw === "object" ? raw : row;
    Object.keys(source || {}).forEach((key) => {
      if (!excluded.has(key) && !seen.has(key)) {
        seen.add(key);
        keys.push(key);
      }
    });
  }
  return keys.map((key, index) => ({ id: index + 1, dbKey: key, name: key }));
}

function parseSortDateTime(value) {
  if (!value) return null;
  if (value instanceof Date && !Number.isNaN(value.getTime())) return value;

  const text = String(value).trim();
  if (!text) return null;

  const parsed = new Date(text);
  if (!Number.isNaN(parsed.getTime())) return parsed;

  const normalized = text.replace(" ", "T");
  const fallback = new Date(normalized);
  if (!Number.isNaN(fallback.getTime())) return fallback;

  return null;
}

function compareAnalysisRecency(left, right) {
  const leftStamp = (parseSortDateTime(left.call_recording_date || left.created_at || left.call_date) || new Date(0)).getTime();
  const rightStamp = (parseSortDateTime(right.call_recording_date || right.created_at || right.call_date) || new Date(0)).getTime();
  return rightStamp - leftStamp;
}

function conditionalFill(dbKey, rawValue) {
  const v = String(rawValue ?? "").trim().toLowerCase();
  const RED = { bg: EXCEL_COLORS.redBg, text: EXCEL_COLORS.redText };
  const AMBER = { bg: EXCEL_COLORS.yellowBg, text: EXCEL_COLORS.yellowText };
  const GREEN = { bg: EXCEL_COLORS.greenBg, text: EXCEL_COLORS.greenText };
  const SLATE = { bg: EXCEL_COLORS.neutralBg, text: EXCEL_COLORS.neutralText };

  if (dbKey === "overall_call_quality_score") {
    const n = parseFloat(v);
    if (!Number.isNaN(n)) {
      if (n >= 8) return GREEN;
      if (n >= 5) return AMBER;
      return RED;
    }
    return null;
  }

  if (v === "yes") return GREEN;
  if (v === "no") return RED;
  if (v.includes("pending") || v.includes("overdue")) return AMBER;
  if (v.includes("complete") || v.includes("done") || v.includes("resolved")) return GREEN;
  if (v.includes("positive")) return GREEN;
  if (v.includes("negative")) return RED;
  if (v.includes("neutral")) return SLATE;
  return null;
}

function buildWorkbook(rows, filters, fields) {
  const lastCol = fields.length - 1;
  const headers = fields.map((field) => field.name);
  const aoa = [
    ["AutoFlint Call Analysis"],
    [`Generated ${new Date().toLocaleString()}`],
    [],
    ["EXPORT SUMMARY"],
    ["Date Range", `${filters.startDate || "All"} → ${filters.endDate || "All"}`],
    ["Exported Rows", rows.length],
    [],
    headers,
    ...rows.map((row) => fields.map((field) => formatCellValue(getFieldValue(row, field.dbKey)))),
  ];

  const ws = XLSX.utils.aoa_to_sheet(aoa);
  ws["!cols"] = fields.map((field) => ({ wch: Math.max(16, Math.min(36, (field.name || field.dbKey).length + 4)) }));
  ws["!merges"] = [
    { s: { r: 0, c: 0 }, e: { r: 0, c: lastCol } },
    { s: { r: 1, c: 0 }, e: { r: 1, c: lastCol } },
    { s: { r: 3, c: 0 }, e: { r: 3, c: lastCol } },
  ];

  const border = () => ({
    top: { style: "thin", color: { rgb: EXCEL_COLORS.border } },
    bottom: { style: "thin", color: { rgb: EXCEL_COLORS.border } },
    left: { style: "thin", color: { rgb: EXCEL_COLORS.border } },
    right: { style: "thin", color: { rgb: EXCEL_COLORS.border } },
  });

  const setCell = (addr, style) => {
    if (ws[addr]) ws[addr].s = style;
  };
  const setRange = (r0, r1, c0, c1, style) => {
    for (let r = r0; r <= r1; r += 1) {
      for (let c = c0; c <= c1; c += 1) {
        setCell(XLSX.utils.encode_cell({ r, c }), style);
      }
    }
  };

  const titleS = { font: { bold: true, sz: 18, color: { rgb: EXCEL_COLORS.titleText } }, fill: { fgColor: { rgb: EXCEL_COLORS.titleBg } }, alignment: { horizontal: "center", vertical: "center" }, border: border() };
  const subtitleS = { font: { italic: true, sz: 11, color: { rgb: EXCEL_COLORS.subtitleText } }, fill: { fgColor: { rgb: EXCEL_COLORS.titleBg } }, alignment: { horizontal: "center", vertical: "center" }, border: border() };
  const headerS = { font: { bold: true, sz: 11, color: { rgb: EXCEL_COLORS.headerText } }, fill: { fgColor: { rgb: EXCEL_COLORS.headerBg } }, alignment: { horizontal: "center", vertical: "center", wrapText: true }, border: border() };

  setRange(0, 0, 0, lastCol, titleS);
  setRange(1, 1, 0, lastCol, subtitleS);
  setRange(7, 7, 0, lastCol, headerS);

  rows.forEach((row, idx) => {
    const excelRow = 8 + idx;
    const rowFill = idx % 2 === 0 ? EXCEL_COLORS.rowBase : EXCEL_COLORS.rowAlt;
    fields.forEach((field, c) => {
      const raw = getFieldValue(row, field.dbKey);
      const isActionFollowUpField = ACTION_FOLLOWUP_FIELD_KEYS.has(field.dbKey);
      const status = isActionFollowUpField ? conditionalFill(field.dbKey, raw) : null;
      setCell(XLSX.utils.encode_cell({ r: excelRow, c }), {
        font: { sz: 10, bold: !!status, color: { rgb: status ? status.text : EXCEL_COLORS.subtitleText } },
        fill: { fgColor: { rgb: status ? status.bg : rowFill } },
        alignment: { horizontal: "left", vertical: "center", wrapText: true },
        border: border(),
      });
    });
  });

  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Call Analysis");
  return wb;
}

function CallAnalysis() {
  const [filters, setFilters] = useState(() => {
    const saved = loadSaved(LS_FILTERS, {});
    return {
      startDate: saved.startDate || DEFAULT_START_DATE,
      endDate: saved.endDate || new Date().toISOString().slice(0, 10),
    };
  });
  const [allRows, setAllRows] = useState(() => loadSaved(LS_DATA, []));
  const [prompts, setPrompts] = useState(() => loadSaved(LS_PROMPTS, []));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [promptError, setPromptError] = useState("");
  const [selectedRows, setSelectedRows] = useState(new Set());
  const [expandedRows, setExpandedRows] = useState(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    localStorage.setItem(LS_FILTERS, JSON.stringify(filters));
  }, [filters]);

  useEffect(() => {
    localStorage.setItem(LS_DATA, JSON.stringify(allRows));
  }, [allRows]);

  useEffect(() => {
    localStorage.setItem(LS_PROMPTS, JSON.stringify(prompts));
  }, [prompts]);

  const analysisFields = useMemo(() => {
    const normalized = prompts.map(normalizePromptField).filter(Boolean);
    if (normalized.length > 0) return normalized;
    return deriveFallbackFields(allRows);
  }, [prompts, allRows]);

  const sortedRows = useMemo(() => [...allRows].sort(compareAnalysisRecency), [allRows]);

  useEffect(() => {
    setSelectedRows(new Set(sortedRows.map((_, index) => index)));
  }, [sortedRows]);

  const glanceFields = useMemo(() => analysisFields.slice(0, 5), [analysisFields]);

  const fetchPrompts = async () => {
    try {
      const data = await getPrompts();
      setPrompts(Array.isArray(data) ? data : []);
      setPromptError("");
    } catch (err) {
      setPromptError(err.message || "Failed to load analysis fields.");
    }
  };

  const fetchData = async (startDate = filters.startDate, endDate = filters.endDate) => {
    if (!startDate || !endDate) {
      setError("Please select both start and end dates.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const data = await getCallAnalysis(startDate, endDate);
      const rows = Array.isArray(data) ? data : data.results || data.items || [];
      setAllRows(rows);
    } catch (err) {
      setError(err.message);
      setAllRows([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPrompts();
  }, []);

  useEffect(() => {
    if (filters.startDate && filters.endDate) {
      fetchData(filters.startDate, filters.endDate);
    }
  }, []);

  const handleDateChange = (key) => (event) => {
    const next = { ...filters, [key]: event.target.value };
    setFilters(next);
    if (next.startDate && next.endDate) fetchData(next.startDate, next.endDate);
  };

  const toggleRow = (idx) => {
    setSelectedRows((prev) => {
      const next = new Set(prev);
      next.has(idx) ? next.delete(idx) : next.add(idx);
      return next;
    });
  };

  const toggleExpand = (idx) => {
    setExpandedRows((prev) => (prev === idx ? null : idx));
  };

  const exportToExcel = () => {
    const toExport = sortedRows.filter((_, index) => selectedRows.has(index));
    if (!toExport.length) {
      alert("No rows selected for export.");
      return;
    }
    const fields = analysisFields.length ? analysisFields : deriveFallbackFields(toExport);
    const workbook = buildWorkbook(toExport, filters, fields);
    XLSX.writeFile(workbook, `call_analysis_${filters.startDate}_${filters.endDate}.xlsx`);
  };

  const toggleFullscreen = () => setIsFullscreen((prev) => !prev);

  return (
    <div className={`call-records-page${isFullscreen ? " fullscreen-mode" : ""}`}>
      <div className="page-header">
        <h1>Call Analysis</h1>
        <button type="button" className="btn-secondary" onClick={toggleFullscreen} title={isFullscreen ? "Exit Fullscreen" : "Fullscreen"}>
          {isFullscreen ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
        </button>
      </div>

      <div className="filters-panel call-filters-panel">
        <div className="filters-row">
          <div className="filter-group filter-group-compact">
            <label>Start Date</label>
            <input className="filter-date-input" type="date" value={filters.startDate} onChange={handleDateChange("startDate")} />
          </div>
          <div className="filter-group filter-group-compact">
            <label>End Date</label>
            <input className="filter-date-input" type="date" value={filters.endDate} onChange={handleDateChange("endDate")} />
          </div>

          <div className="filter-actions">
            <button type="button" className="btn-export filter-export-btn" onClick={exportToExcel} disabled={allRows.length === 0}>
              <FileSpreadsheet size={16} strokeWidth={2.4} style={{ marginRight: "6px" }} />
              Export to Excel
              {allRows.length > 0 ? ` (${selectedRows.size})` : ""}
            </button>
          </div>
        </div>
      </div>

      {promptError && <div className="error-message">{promptError}</div>}
      {error && <div className="error-message">{error}</div>}

      <div className="table-wrapper">
        <div className="table-scroll analysis-scroll">
          <div className="analysis-list">
            <div
              className="analysis-card-header analysis-list-headrow"
              style={{
                cursor: "default",
                fontWeight: 600,
                fontSize: "0.85rem",
                color: "#94A3B8",
                borderBottom: "1px solid #334155",
                background: "#18212F",
                display: "flex",
                alignItems: "center",
                width: "100%",
                gap: "12px",
                padding: "10px 16px",
                position: "sticky",
                top: 0,
                zIndex: 20,
                minHeight: 44,
                boxSizing: "border-box",
              }}
            >
              <div className="col-check" style={{ flex: "0 0 auto" }} />
              <span style={{ color: "#94A3B8", fontWeight: 600, flex: 1 }}>Call ID</span>
              {glanceFields.map((field) => (
                <span key={field.dbKey} className="glance-value" style={{ color: "#94A3B8", fontWeight: 600, flex: 1 }}>
                  {field.name}
                </span>
              ))}
              <span style={{ flex: "0 0 auto", width: 90 }} />
            </div>

            {sortedRows.map((row, index) => {
              const isOpen = expandedRows === index;
              return (
                <div key={row.id ?? index} className={`analysis-card${isOpen ? " open" : ""}`}>
                  <div
                    className="analysis-card-header"
                    onClick={() => toggleExpand(index)}
                    role="button"
                    tabIndex={0}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      width: "100%",
                      gap: "12px",
                      padding: "10px 16px",
                      ...(isOpen
                        ? {
                            position: "sticky",
                            top: 44,
                            zIndex: 15,
                            background: "#1a2235",
                            boxShadow: "0 1px 0 #2d3748",
                          }
                        : {}),
                    }}
                  >
                    <div className="col-check" style={{ flex: "0 0 auto" }} onClick={(event) => event.stopPropagation()}>
                      <input type="checkbox" className="row-checkbox" checked={selectedRows.has(index)} onChange={() => toggleRow(index)} />
                    </div>
                    <span style={{ color: "#FFFFFF", fontWeight: 600, flex: 1 }}>{row.formatted_call_id ?? row.call_id ?? "—"}</span>
                    {glanceFields.map((field) => {
                      const value = getFieldValue(row, field.dbKey);
                      return (
                        <span key={field.dbKey} className="glance-value" style={{ flex: 1 }} title={formatCellValue(value)}>
                          {value != null && value !== "" ? formatCellValue(value) : <span className="value-empty">—</span>}
                        </span>
                      );
                    })}
                    <button
                      type="button"
                      className="analysis-expand-btn"
                      onClick={(event) => {
                        event.stopPropagation();
                        toggleExpand(index);
                      }}
                      style={{
                        flex: "0 0 auto",
                        width: 90,
                        padding: "6px 10px",
                        fontSize: "0.8rem",
                        fontWeight: 600,
                        borderRadius: "6px",
                        border: "1px solid #334155",
                        background: isOpen ? "#2563EB" : "transparent",
                        color: isOpen ? "#FFFFFF" : "#94A3B8",
                        cursor: "pointer",
                      }}
                    >
                      {isOpen ? "Collapse" : "Expand"}
                    </button>
                  </div>

                  {isOpen && (
                    <div className="analysis-card-body">
                      <div className="analysis-section">
                        <div className="section-badge section-a">Analysis Fields</div>
                        <div className="field-grid">
                          {analysisFields.map((field) => {
                            const value = getFieldValue(row, field.dbKey);
                            const hasValue = value != null && value !== "";
                            return (
                              <div key={field.dbKey} className="field-box">
                                <div className="field-label" title={field.notes || field.dbKey}>{field.name}</div>
                                <div className="field-value">
                                  {hasValue ? formatCellValue(value) : <span className="value-empty">—</span>}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}

            {allRows.length === 0 && !loading && (
              <div className="no-data">{error ? "" : "No records found. Select a date range to load data."}</div>
            )}
          </div>
        </div>
      </div>

      <BufferingOverlay loading={loading} text="Fetching call analysis..." />
    </div>
  );
}

export default CallAnalysis;