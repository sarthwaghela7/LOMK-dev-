import { useState, useEffect, useMemo, useRef, Fragment } from "react";
import { API_BASE_URL, getCalls, getCallAnalysis, getPrompts, getCallContent } from "../data/api";
import { BufferingOverlay } from "../components/BufferingLoader";
// NOTE: Removed unused `Info` import (eslint no-unused-vars) — it was never
// referenced anywhere in this component.
import { Play, Download, Eye, Copy, Check, ChevronDown, X } from "lucide-react";
import * as XLSX from "xlsx";

const LS_FILTERS = "call_records_filters";
const LS_CALLS = "call_records_data";

// Defaults the date filters to yesterday (start === end) so the page never
// opens with an unfiltered/empty date range.
function getYesterdayISO() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

// Base call-record columns. These come straight off the call object.
const BASE_EXPORT_COLUMNS = [
  {
    label: "Call ID",
    getter: (call) => getField(call, "formatted_call_id", "call_id"),
    width: 12,
  },
  {
    label: "Date",
    getter: (call) => getField(call, "date", "call_date", "created_at"),
    width: 14,
  },
  {
    label: "Time",
    getter: (call) => getField(call, "time", "call_time", "timestamp"),
    width: 12,
  },
  {
    label: "Source",
    getter: (call) => getField(call, "src", "source", "from_number", "caller_id", "from"),
    width: 20,
  },
  {
    label: "Destination",
    getter: (call) => getField(call, "dst", "destination", "to_number", "dialed_number", "to"),
    width: 20,
  },
  {
    label: "Extension",
    getter: (call) => getField(call, "extension", "ext"),
    width: 14,
  },
  {
    label: "Duration",
    getter: (call) => getField(call, "billsec", "duration", "call_duration"),
    width: 14,
  },
  {
    label: "Status",
    getter: (call) => getProcessingStatus(call),
    width: 14,
  },
  {
    label: "Transcript",
    getter: (call) => getField(call, "transcript"),
    width: 40,
  },
  {
    label: "Audio",
    getter: (call) => getField(call, "audio_s3_uri", "audio_s3_key"),
    width: 18,
  },
];

// Builds the full column list for export: base call-record columns followed
// by every dynamic Call Analysis field (matched to a call via any of its
// candidate ref fields — call_reference, formatted_call_id, call_id, or
// call_number).
function buildExportColumns(analysisFields, analysisByRef) {
  const dynamicColumns = analysisFields.map((field) => ({
    label: field.name || field.dbKey,
    getter: (call) => {
      const analysisRow = resolveAnalysisRowForCall(call, analysisByRef);
      if (!analysisRow) return "—";
      return formatAnalysisValue(getAnalysisFieldValue(analysisRow, field.dbKey));
    },
    width: Math.max(16, Math.min(40, (field.name || field.dbKey).length + 4)),
  }));
  return [...BASE_EXPORT_COLUMNS, ...dynamicColumns];
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

function compareCallRecency(left, right) {
  const leftDate = parseSortDateTime(getField(left, "call_date", "date", "created_at"));
  const rightDate = parseSortDateTime(getField(right, "call_date", "date", "created_at"));
  const leftTime = parseSortDateTime(`${getField(left, "call_date", "date", "created_at") || ""} ${getField(left, "call_time", "time", "timestamp") || ""}`);
  const rightTime = parseSortDateTime(`${getField(right, "call_date", "date", "created_at") || ""} ${getField(right, "call_time", "time", "timestamp") || ""}`);

  const leftStamp = (leftTime || leftDate || new Date(0)).getTime();
  const rightStamp = (rightTime || rightDate || new Date(0)).getTime();

  return rightStamp - leftStamp;
}

// Simple export theme: plain white sheet, colored heading row only.
const EXCEL_COLORS = {
  headerBg: "2563EB",
  headerText: "FFFFFF",
  border: "D1D5DB",
};

function buildWorkbook(callsToExport, filters, columns) {
  const totalColumns = columns.length;
  const lastColumnIndex = totalColumns - 1;
  const headerRow = 0; // header is the very first row, no title/summary block

  const aoa = [
    columns.map((column) => column.label),
    ...callsToExport.map((call) => columns.map((column) => column.getter(call))),
  ];

  const ws = XLSX.utils.aoa_to_sheet(aoa);

  ws["!cols"] = columns.map((column) => ({ wch: column.width }));
  const lastColumnLetter = XLSX.utils.encode_col(lastColumnIndex);
  ws["!autofilter"] = { ref: `A1:${lastColumnLetter}${callsToExport.length + 1}` };
  ws["!rows"] = [{ hpt: 22 }];

  const makeBorder = () => ({
    top: { style: "thin", color: { rgb: EXCEL_COLORS.border } },
    bottom: { style: "thin", color: { rgb: EXCEL_COLORS.border } },
    left: { style: "thin", color: { rgb: EXCEL_COLORS.border } },
    right: { style: "thin", color: { rgb: EXCEL_COLORS.border } },
  });

  const setCellStyle = (address, style) => {
    if (ws[address]) {
      ws[address].s = style;
    }
  };

  const setRangeStyle = (startRow, endRow, startCol, endCol, styleFactory) => {
    for (let row = startRow; row <= endRow; row += 1) {
      for (let col = startCol; col <= endCol; col += 1) {
        const address = XLSX.utils.encode_cell({ r: row, c: col });
        setCellStyle(address, styleFactory(row, col));
      }
    }
  };

  const headerStyle = {
    font: { bold: true, sz: 11, color: { rgb: EXCEL_COLORS.headerText } },
    fill: { fgColor: { rgb: EXCEL_COLORS.headerBg } },
    alignment: { horizontal: "center", vertical: "center" },
    border: makeBorder(),
  };
  // Plain white body cells — no shading, no conditional colors.
  const bodyStyle = {
    font: { sz: 10, color: { rgb: "111827" } },
    fill: { fgColor: { rgb: "FFFFFF" } },
    alignment: { horizontal: "center", vertical: "center" },
    border: makeBorder(),
  };

  setRangeStyle(headerRow, headerRow, 0, lastColumnIndex, () => headerStyle);
  setRangeStyle(1, callsToExport.length, 0, lastColumnIndex, () => bodyStyle);

  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Call Records");
  return wb;
}

// Normalize a phone number for comparison: strip all non-digit characters,
// then drop a leading US/Canada country code ("1") if present, so numbers
// entered/stored with or without a "+1"/"1" prefix compare equal.
function normalizePhoneForSearch(raw) {
  const digits = String(raw || "").replace(/\D/g, "");
  if (digits.length === 11 && digits.startsWith("1")) return digits.slice(1);
  return digits;
}

// Quick-pick numbers offered in the "Search by Number" field. A call is
// "outbound" when one of these appears in src, "inbound" when one appears
// in dst.
const QUICK_SEARCH_NUMBERS = ["2243374447", "3128781255", "101", "102", "105"];
const QUICK_SEARCH_NUMBERS_NORMALIZED = QUICK_SEARCH_NUMBERS.map(normalizePhoneForSearch);
const EXTENSION_OPTIONS = ["6304452050"];

const getField = (call, ...keys) => {
  if (!call || typeof call !== "object") return "—";
  for (const key of keys) {
    if (call[key] !== undefined && call[key] !== null) return call[key];
  }
  return "—";
};

const getProcessingStatus = (call) => {
  const rawStatus = String(getField(call, "processing_status", "ai_completed", "completed", "processed") || "").toLowerCase();
  if (rawStatus === "processed" || rawStatus === "complete" || rawStatus === "completed" || rawStatus === "true") {
    return "Processed";
  }
  if (rawStatus === "not_processed" || rawStatus === "not processed" || rawStatus === "pending" || rawStatus === "false") {
    return "Not Processed";
  }
  return rawStatus ? rawStatus.replace(/_/g, " ") : "Not Processed";
};

// ---- Call Analysis lookup helpers (mirrors CallAnalysis.jsx parsing) ----

const ANALYSIS_EXCLUDED_KEYS = new Set([
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

function formatAnalysisValue(value) {
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

function isLongAnalysisValue(value) {
  if (value === null || value === undefined || value === "") return false;
  const formatted = formatAnalysisValue(value);
  return typeof value === "object" || formatted.length > 140 || formatted.includes("\n");
}

function getAnalysisFieldValue(row, key) {
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

// A call record and its matching analysis row don't always agree on which
// id field is populated — call_reference is frequently missing on the call
// object returned by getCalls(), even though it's what the analysis row is
// keyed by. Build the full set of candidate identifiers for an object (call
// OR analysis row) so lookups can try every one of them instead of only
// call_reference, which was silently making every call_reference-based
// lookup miss whenever that field was absent.
function getCallRefCandidates(obj) {
  if (!obj || typeof obj !== "object") return [];
  const candidates = [
    obj.call_reference,
    obj.formatted_call_id,
    obj.call_id,
    obj.call_number,
  ];
  return candidates
    .filter((v) => v !== undefined && v !== null && v !== "" && v !== "—")
    .map((v) => String(v));
}

// Resolve the analysis row for a given call by trying every candidate ref
// the call carries against the analysisByRef map (itself keyed by every
// candidate ref each analysis row carries — see analysisByRef below).
function resolveAnalysisRowForCall(call, analysisByRef) {
  for (const ref of getCallRefCandidates(call)) {
    const row = analysisByRef.get(ref);
    if (row) return row;
  }
  return undefined;
}

function normalizeAnalysisPromptField(prompt) {
  const key = (prompt?.key || "").trim();
  if (!key || prompt?.id === 1) return null;
  return {
    id: prompt.id,
    dbKey: key,
    name: key,
    notes: (prompt?.notes || "").trim(),
  };
}

function deriveFallbackAnalysisFields(rows) {
  const seen = new Set();
  const keys = [];
  for (const row of rows) {
    const raw = parseMaybeJson(row.raw_llm_json);
    const source = raw && typeof raw === "object" ? raw : row;
    Object.keys(source || {}).forEach((key) => {
      if (!ANALYSIS_EXCLUDED_KEYS.has(key) && !seen.has(key)) {
        seen.add(key);
        keys.push(key);
      }
    });
  }
  return keys.map((key, index) => ({ id: index + 1, dbKey: key, name: key }));
}

// Force these two fields to always render last in the expanded KPI
// grid: overall_call_quality_score second-to-last, evaluator_notes last.
// Also move CLIENT_RESPONSE to sit right next to PAID_CONSULTATION_PITCHED
// (checks both possible prompt key spellings for that field).
function reorderAnalysisFields(fields) {
  const pinnedKeys = ["overall_call_quality_score", "evaluator_notes"];
  const clientResponseKeys = ["client_response", "client_response_to_consultation"];

  let working = fields;
  const clientResponseField = fields.find((field) => clientResponseKeys.includes(field.dbKey));
  if (clientResponseField) {
    const withoutClientResponse = fields.filter((field) => field !== clientResponseField);
    const pitchIndex = withoutClientResponse.findIndex((field) => field.dbKey === "paid_consultation_pitched");
    if (pitchIndex !== -1) {
      working = [
        ...withoutClientResponse.slice(0, pitchIndex + 1),
        clientResponseField,
        ...withoutClientResponse.slice(pitchIndex + 1),
      ];
    }
  }

  const rest = working.filter((field) => !pinnedKeys.includes(field.dbKey));
  const pinned = pinnedKeys
    .map((key) => working.find((field) => field.dbKey === key))
    .filter(Boolean);
  return [...rest, ...pinned];
}

function loadSavedFilters() {
  try {
    const saved = localStorage.getItem(LS_FILTERS);
    if (saved) {
      const parsed = JSON.parse(saved);
      return {
        startDate: parsed.startDate || getYesterdayISO(),
        endDate: parsed.endDate || getYesterdayISO(),
        searchNumber: parsed.searchNumber || "",
        extension: parsed.extension || "",
        callId: parsed.callId || "",
        source: parsed.source || "",
        destination: parsed.destination || "",
        frontDeskPersonName: parsed.frontDeskPersonName || "",
        processingStatus: parsed.processingStatus || "",
        callDirection: parsed.callDirection || "",
      };
    }
  } catch { /* ignore */ }
  return {
    startDate: getYesterdayISO(),
    endDate: getYesterdayISO(),
    searchNumber: "",
    extension: "",
    callId: "",
    source: "",
    destination: "",
    frontDeskPersonName: "",
    processingStatus: "",
    callDirection: "",
  };
}

// NOTE: We intentionally do NOT cache the full call dataset (LS_CALLS) in
// localStorage — it can easily exceed the browser's storage quota once
// transcripts/audio refs for a date range pile up (this was the cause of
// the QuotaExceededError on LS_CALLS). The saved filters (LS_FILTERS)
// already persist the selected date range, and fetchCalls() always
// re-requests fresh data from the API on mount, so a localStorage cache of
// the full dataset was redundant. We still clean up any stale entry left
// over from before this fix so it doesn't keep consuming quota.
function loadSavedCalls() {
  try {
    localStorage.removeItem(LS_CALLS);
  } catch { /* ignore */ }
  return [];
}

// A blob download always uses the `download` attribute's filename, ignoring
// any Content-Disposition header the server sent — so the name has to be
// built client-side too, using the same "AF#<id>_<date>" convention the
// backend's own /audio/{call_id} endpoint uses for its presigned filename.
const buildAudioDownloadFilename = (call, extension) => {
  const label = getField(call, "call_reference", "formatted_call_id", "call_id") || "call";
  const callDate = getField(call, "call_date", "date");
  const base = callDate ? `${label}_${callDate}` : label;
  return `${base.replace(/[\\/:*?"<>|]/g, "-")}.${extension}`;
};

const getAudioSourceFromCall = (call, fallbackUrl = "") => {
  const explicitUrl = fallbackUrl || getField(call, "audio_url", "audio_file_url", "audioUrl");
  if (typeof explicitUrl === "string" && explicitUrl.trim() && explicitUrl !== "—") {
    const trimmed = explicitUrl.trim();
    if (/^https?:\/\//i.test(trimmed)) return trimmed;
    if (trimmed.startsWith("/")) return `${API_BASE_URL}${trimmed}`;
  }

  const audioRefValue = getField(call, "audio_s3_uri", "audio_s3_key", "audio_path", "audio_file");
  if (typeof audioRefValue === "string" && audioRefValue.trim() && audioRefValue !== "—") {
    const trimmed = audioRefValue.trim();
    if (/^https?:\/\//i.test(trimmed)) return trimmed;
    if (trimmed.startsWith("/")) return `${API_BASE_URL}${trimmed}`;
    if (!trimmed.startsWith("s3://")) return trimmed;
  }

  return "";
};

function CallRecords() {
  const [filters, setFilters] = useState(loadSavedFilters);
  const [allCalls, setAllCalls] = useState(loadSavedCalls);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // Track which row indices are selected (all ticked by default)
  const [selectedRows, setSelectedRows] = useState(new Set());
  const [audioModalCall, setAudioModalCall] = useState(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [audioLoading, setAudioLoading] = useState(false);
  const [audioError, setAudioError] = useState("");
  const [audioDownloadLoading, setAudioDownloadLoading] = useState(false);
  const [cellModal, setCellModal] = useState(null);
  const [transcriptModalCall, setTranscriptModalCall] = useState(null);
  const [copiedTranscript, setCopiedTranscript] = useState(false);
  const [analysisRows, setAnalysisRows] = useState([]);
  const [analysisPrompts, setAnalysisPrompts] = useState([]);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [expandedRow, setExpandedRow] = useState(null);
  // NOTE: Tracks the last `calls` value we reset selection for for the
  // render-time "adjust state during render" pattern above (replaces the
  // old useEffect-based reset).
  const [prevCallsForSelection, setPrevCallsForSelection] = useState([]);
  const audioRef = useRef(null);
  const audioCacheRef = useRef(new Map());
  const AUDIO_CACHE_TTL_MS = 5 * 60 * 1000;

  // Persist filters
  // NOTE: Wrapped in try/catch (previously unguarded) so that if the
  // origin's localStorage is still full for any other reason, saving these
  // small filters can't throw and crash the app — it just skips the cache.
  useEffect(() => {
    try {
      localStorage.setItem(LS_FILTERS, JSON.stringify(filters));
    } catch (error) {
      if (error instanceof DOMException && error.name === "QuotaExceededError") {
        console.warn("localStorage quota exceeded. Skipping filter cache.");
      } else {
        throw error;
      }
    }
  }, [filters]);

  // NOTE: We no longer persist the full call dataset (allCalls) to
  // localStorage — see loadSavedCalls() above for why. Only lightweight
  // filters are cached; call data itself always comes fresh from the API.

  useEffect(() => {
    if (!audioModalCall) return undefined;

    const handleEscape = (event) => {
      if (event.key === "Escape") {
        closeAudioModal();
      }
    };

    window.addEventListener("keydown", handleEscape);
    document.body.style.overflow = "hidden";

    return () => {
      window.removeEventListener("keydown", handleEscape);
      document.body.style.overflow = "";
    };
  }, [audioModalCall]);

  useEffect(() => {
    if (!transcriptModalCall) return undefined;

    const handleEscape = (event) => {
      if (event.key === "Escape") {
        closeTranscriptModal();
      }
    };

    window.addEventListener("keydown", handleEscape);
    document.body.style.overflow = "hidden";

    return () => {
      window.removeEventListener("keydown", handleEscape);
      document.body.style.overflow = "";
    };
  }, [transcriptModalCall]);

  useEffect(() => {
    if (!audioUrl || !audioRef.current) return undefined;

    const audio = audioRef.current;
    audio.load();
    const playPromise = audio.play();
    if (playPromise && typeof playPromise.catch === "function") {
      playPromise.catch(() => undefined);
    }
    return undefined;
  }, [audioUrl, audioModalCall]);

  useEffect(() => {
    // NOTE: Capture the Map reference now (inside the effect body) rather
    // than reading `audioCacheRef.current` inside the cleanup function.
    // eslint (react-hooks/exhaustive-deps) warns that `.current` could
    // have changed by the time cleanup runs; capturing it here removes
    // that ambiguity while keeping the same unmount-cleanup behavior.
    const cache = audioCacheRef.current;
    return () => {
      cache.forEach((entry) => URL.revokeObjectURL(entry.url));
      cache.clear();
    };
  }, []);

  // Lookup: call ref (call_reference, formatted_call_id, call_id, or
  // call_number — whichever the row actually has) -> analysis row.
  const analysisByRef = useMemo(() => {
    const map = new Map();
    analysisRows.forEach((row) => {
      getCallRefCandidates(row).forEach((ref) => {
        if (!map.has(ref)) map.set(ref, row);
      });
    });
    return map;
  }, [analysisRows]);

  // Client-side filter by extension, call ID, source, and destination
  const calls = useMemo(() => {
    const searchNumberDigits = normalizePhoneForSearch(filters.searchNumber);
    const extSearch = filters.extension.trim().toLowerCase();
    const callIdSearch = filters.callId.trim().toLowerCase();
    const sourceSearch = filters.source.trim().toLowerCase();
    const destinationSearch = filters.destination.trim().toLowerCase();
    const frontDeskPersonNameSearch = filters.frontDeskPersonName.trim().toLowerCase();
    const processingStatusFilter = filters.processingStatus;

    const filtered = allCalls.filter((call) => {
      if (processingStatusFilter) {
        const status = getProcessingStatus(call);
        if (status !== processingStatusFilter) return false;
      }
      if (searchNumberDigits) {
        const srcDigits = normalizePhoneForSearch(
          getField(call, "src", "source", "from_number", "caller_id", "from")
        );
        const dstDigits = normalizePhoneForSearch(
          getField(call, "dst", "destination", "to_number", "dialed_number", "to")
        );
        const matchesSrc = srcDigits && srcDigits.includes(searchNumberDigits);
        const matchesDst = dstDigits && dstDigits.includes(searchNumberDigits);
        if (!matchesSrc && !matchesDst) return false;
      }
      if (filters.callDirection) {
        const srcDigits = normalizePhoneForSearch(
          getField(call, "src", "source", "from_number", "caller_id", "from")
        );
        const dstDigits = normalizePhoneForSearch(
          getField(call, "dst", "destination", "to_number", "dialed_number", "to")
        );
        const isOutbound = QUICK_SEARCH_NUMBERS_NORMALIZED.includes(srcDigits);
        const isInbound = QUICK_SEARCH_NUMBERS_NORMALIZED.includes(dstDigits);
        if (filters.callDirection === "outbound" && !isOutbound) return false;
        if (filters.callDirection === "inbound" && !isInbound) return false;
      }
      if (extSearch) {
        const ext = String(getField(call, "extension", "ext") || "").toLowerCase();
        if (!ext.includes(extSearch)) return false;
      }
      if (callIdSearch) {
        const callId = String(
          getField(call, "formatted_call_id", "call_id") || ""
        ).toLowerCase();
        if (!callId.includes(callIdSearch)) return false;
      }
      if (sourceSearch) {
        const src = String(
          getField(call, "src", "source", "from_number", "caller_id", "from") || ""
        ).toLowerCase();
        if (!src.includes(sourceSearch)) return false;
      }
      if (destinationSearch) {
        const dst = String(
          getField(call, "dst", "destination", "to_number", "dialed_number", "to") || ""
        ).toLowerCase();
        if (!dst.includes(destinationSearch)) return false;
      }
      if (frontDeskPersonNameSearch) {
        const analysisRow = resolveAnalysisRowForCall(call, analysisByRef);
        const frontDeskPersonName = String(
          getAnalysisFieldValue(analysisRow, "front_desk_person_name") || ""
        ).toLowerCase();
        if (!frontDeskPersonName.includes(frontDeskPersonNameSearch)) return false;
      }
      return true;
    });

    return [...filtered].sort(compareCallRecency);
  }, [allCalls, filters.searchNumber, filters.extension, filters.callId, filters.source, filters.destination, filters.frontDeskPersonName, filters.processingStatus, filters.callDirection, analysisByRef]);

  // NOTE: This used to be a `useEffect` placed above the `calls` useMemo,
  // which (a) referenced `calls` before it was declared (eslint
  // react-hooks/immutability error) and (b) called setState synchronously
  // inside an effect purely to derive state from `calls` (eslint
  // react-hooks/set-state-in-effect error). Both are resolved by using
  // React's recommended "adjust state during render" pattern instead of an
  // effect: compare `calls` to the previously-seen value and update
  // selection state directly in the render body when it changes. Same
  // behavior (reset selection to "all" whenever the filtered/sorted call
  // list changes), no effect needed.
  if (calls !== prevCallsForSelection) {
    setPrevCallsForSelection(calls);
    setSelectedRows(new Set(calls.map((_, idx) => idx)));
    setExpandedRow(null);
  }

  const fetchCalls = async (startDate = filters.startDate, endDate = filters.endDate, searchNumber = filters.searchNumber) => {
    if (!startDate || !endDate) {
      setError("Please select both start and end dates");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const params = {};
      if (searchNumber && searchNumber.trim()) params.search_number = searchNumber.trim();
      const data = await getCalls(startDate, endDate, params);
      const callsArray = Array.isArray(data) ? data : data.calls || data.results || [];
      setAllCalls(callsArray);
    } catch (err) {
      setError(err.message);
      setAllCalls([]);
    } finally {
      setLoading(false);
    }
  };

  // NOTE: `fetchCalls`/`fetchAnalysis` internally call setState (that's how
  // fetched data reaches the UI), which the newer eslint
  // react-hooks/set-state-in-effect rule flags even though this is the
  // standard "fetch data once on mount" pattern. The effect intentionally
  // has an empty dependency array (react-hooks/exhaustive-deps warning) so
  // it runs exactly once on mount using the initial (persisted) filters —
  // adding `fetchCalls`/`fetchAnalysis`/filters to the deps would refetch
  // on every render/filter change and duplicate the debounced effect below.
  // Suppressing both rules here rather than restructuring the fetch flow,
  // which is out of scope for this fix and risks introducing duplicate or
  // looping requests. Using a block disable (not disable-next-line) since
  // the actual flagged lines are inside the effect body, not the line
  // immediately following this comment.
  /* eslint-disable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */
  useEffect(() => {
    if (filters.startDate && filters.endDate) {
      fetchCalls(filters.startDate, filters.endDate, filters.searchNumber);
      fetchAnalysis(filters.startDate, filters.endDate);
    }
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */

  // NOTE: Same reasoning as above — this effect intentionally re-runs only
  // when `filters.searchNumber` changes (debounced), not on every render,
  // so `fetchCalls`/other filter fields are deliberately omitted from deps.
  // Block disable used for the same reason as above (flagged line is the
  // closing deps array, not the line right after this comment).
  /* eslint-disable react-hooks/exhaustive-deps */
  useEffect(() => {
    if (!filters.startDate || !filters.endDate) return;
    const timer = setTimeout(() => {
      fetchCalls(filters.startDate, filters.endDate, filters.searchNumber);
    }, 250);
    return () => clearTimeout(timer);
  }, [filters.searchNumber]);
  /* eslint-enable react-hooks/exhaustive-deps */

  useEffect(() => {
    getPrompts()
      .then((data) => setAnalysisPrompts(Array.isArray(data) ? data : []))
      .catch(() => setAnalysisPrompts([]));
  }, []);

  // NOTE: Changed from `const fetchAnalysis = async (...) => {...}` to a
  // hoisted function declaration. It's called from the mount useEffect
  // above before this line was reached textually, which eslint
  // (react-hooks/immutability) flags as "accessed before declared".
  // Function declarations are hoisted, so this resolves the error without
  // needing to reorder any code.
  async function fetchAnalysis(startDate = filters.startDate, endDate = filters.endDate) {
    if (!startDate || !endDate) return;
    setAnalysisLoading(true);
    try {
      const data = await getCallAnalysis(startDate, endDate);
      const rows = Array.isArray(data) ? data : data.results || data.items || [];
      setAnalysisRows(rows);
    } catch {
      setAnalysisRows([]);
    } finally {
      setAnalysisLoading(false);
    }
  }

  // Analysis fields to render inside the expanded panel
  const analysisFields = useMemo(() => {
    const normalized = analysisPrompts.map(normalizeAnalysisPromptField).filter(Boolean);
    const fields = normalized.length > 0 ? normalized : deriveFallbackAnalysisFields(analysisRows);
    return reorderAnalysisFields(fields);
  }, [analysisPrompts, analysisRows]);

  const toggleExpandedRow = (idx) => {
    setExpandedRow((prev) => (prev === idx ? null : idx));
  };

  // Export ONLY selected calls
  const exportToExcel = () => {
    const callsToExport = calls.filter((_, idx) => selectedRows.has(idx));
    if (!callsToExport.length) {
      alert("No calls selected for export.");
      return;
    }
    const columns = buildExportColumns(analysisFields, analysisByRef);
    const wb = buildWorkbook(callsToExport, filters, columns);
    XLSX.writeFile(wb, `calls_${filters.startDate}_${filters.endDate}.xlsx`);
  };

  const toggleRow = (idx) => {
    setSelectedRows((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) {
        next.delete(idx);
      } else {
        next.add(idx);
      }
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelectedRows((prev) =>
      prev.size === calls.length ? new Set() : new Set(calls.map((_, idx) => idx))
    );
  };

  // NOTE: Changed from `const closeAudioModal = () => {...}` to a hoisted
  // function declaration. It's referenced earlier in the component (inside
  // the Escape-key useEffect above) before this line was reached textually,
  // which eslint (react-hooks/immutability) flags as "accessed before
  // declared". Function declarations are hoisted, so this resolves the
  // error without needing to reorder any code.
  function closeAudioModal() {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }
    setAudioModalCall(null);
    setAudioUrl("");
    setAudioLoading(false);
    setAudioError("");
  }

  // NOTE: Same hoisting fix as closeAudioModal above — referenced earlier
  // in the component (inside the other Escape-key useEffect) before this
  // line.
  function closeTranscriptModal() {
    setTranscriptModalCall(null);
    setCopiedTranscript(false);
  }
  
  const closeCellModal = () => setCellModal(null);

  const handleCopyTranscript = (transcript) => {
    navigator.clipboard.writeText(transcript);
    setCopiedTranscript(true);
    setTimeout(() => setCopiedTranscript(false), 2000);
  };

  const getAudioBlobUrl = async (call) => {
    const callId = getField(call, "call_id");
    if (!callId || callId === "—") return null;

    const cached = audioCacheRef.current.get(callId);
    if (cached && cached.expiresAt > Date.now()) {
      return cached.url;
    }

    const token = localStorage.getItem("autoflint_token");
    const response = await fetch(`${API_BASE_URL}/calls/audio?call_id=${encodeURIComponent(callId)}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });

    if (!response.ok) {
      throw new Error("Unable to load audio.");
    }

    const blob = await response.blob();
    const blobUrl = URL.createObjectURL(blob);

    if (cached?.url) {
      URL.revokeObjectURL(cached.url);
    }

    audioCacheRef.current.set(callId, {
      url: blobUrl,
      expiresAt: Date.now() + AUDIO_CACHE_TTL_MS,
    });

    return blobUrl;
  };

  const handlePlayAudio = async (call) => {
    const directAudioUrl = getAudioSourceFromCall(call);

    setAudioModalCall(call);
    setAudioUrl("");
    setAudioError("");
    setAudioLoading(true);

    try {
      const blobUrl = await getAudioBlobUrl(call);
      if (blobUrl) {
        setAudioUrl(blobUrl);
      } else if (directAudioUrl) {
        setAudioUrl(directAudioUrl);
      } else {
        setAudioError("Audio is not available for this call.");
      }
    } catch (err) {
      if (directAudioUrl) {
        setAudioUrl(directAudioUrl);
      } else {
        setAudioError(err.message || "Unable to load audio.");
      }
    } finally {
      setAudioLoading(false);
    }
  };

  const handleDownloadAudio = async () => {
    if (!audioModalCall) return;

    const callId = getField(audioModalCall, "call_id");
    if (!callId || callId === "—") return;

    setAudioDownloadLoading(true);
    try {
      if (audioUrl.startsWith("blob:")) {
        const link = document.createElement("a");
        link.href = audioUrl;
        link.download = buildAudioDownloadFilename(audioModalCall, "mp3");
        document.body.appendChild(link);
        link.click();
        link.remove();
        return;
      }

      const token = localStorage.getItem("autoflint_token");
      const response = await fetch(`${API_BASE_URL}/calls/audio?call_id=${encodeURIComponent(callId)}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });

      if (!response.ok) {
        throw new Error("Unable to download audio.");
      }

      const blob = await response.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = blobUrl;
      const extension = blob.type.includes("mpeg") ? "mp3" : blob.type.includes("wav") ? "wav" : "audio";
      link.download = buildAudioDownloadFilename(audioModalCall, extension);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(blobUrl);
    } catch (err) {
      setAudioError(err.message || "Unable to download audio.");
    } finally {
      setAudioDownloadLoading(false);
    }
  };

  const handleDateChange = (key) => (e) => {
    const value = e.target.value;
    const nextFilters = { ...filters, [key]: value };
    setFilters(nextFilters);

    if (nextFilters.startDate && nextFilters.endDate) {
      fetchCalls(nextFilters.startDate, nextFilters.endDate);
      fetchAnalysis(nextFilters.startDate, nextFilters.endDate);
    }
  };

  const hasActiveFilters =
    filters.searchNumber || filters.extension || filters.callId || filters.source || filters.destination || filters.frontDeskPersonName || filters.processingStatus || filters.callDirection;

  const clearAllFilters = () => {
    setFilters((f) => ({
      ...f,
      searchNumber: "",
      extension: "",
      callId: "",
      source: "",
      destination: "",
      frontDeskPersonName: "",
      processingStatus: "",
      callDirection: "",
    }));
  };

  return (
    <div className="call-records-page">
      <div className="page-header">
        <h1>Call Records</h1>
      </div>

      <div className="filters-panel call-filters-panel">
        <div className="filters-row">
          <div className="filter-group filter-group-compact">
            <label>Start Date</label>
            <input
              className="filter-date-input"
              type="date"
              value={filters.startDate}
              onChange={handleDateChange("startDate")}
            />
          </div>
          <div className="filter-group filter-group-compact">
            <label>End Date</label>
            <input
              className="filter-date-input"
              type="date"
              value={filters.endDate}
              onChange={handleDateChange("endDate")}
            />
          </div>
          <div className="filter-group filter-group-extension">
            <label>Search by Number</label>
            <input
              type="text"
              list="quick-search-numbers"
              placeholder="Search src/dst..."
              value={filters.searchNumber}
              onChange={(e) => setFilters((f) => ({ ...f, searchNumber: e.target.value }))}
            />
            <datalist id="quick-search-numbers">
              {QUICK_SEARCH_NUMBERS.map((num) => (
                <option key={num} value={num} />
              ))}
            </datalist>
          </div>
          <div className="filter-group filter-group-compact">
            <label>Direction</label>
            <select
              className="filter-date-input"
              value={filters.callDirection}
              onChange={(e) => setFilters((f) => ({ ...f, callDirection: e.target.value }))}
            >
              <option value="">All</option>
              <option value="inbound">Inbound</option>
              <option value="outbound">Outbound</option>
            </select>
          </div>
          <div className="filter-group filter-group-extension">
            <label>Extension</label>
            <input
              type="text"
              list="call-extension-options"
              placeholder="Filter by extension..."
              value={filters.extension}
              onChange={(e) => setFilters((f) => ({ ...f, extension: e.target.value }))}
            />
            <datalist id="call-extension-options">
              {EXTENSION_OPTIONS.map((extension) => (
                <option key={extension} value={extension} />
              ))}
            </datalist>
          </div>
          <div className="filter-group filter-group-extension">
            <label>Call ID</label>
            <input
              type="text"
              placeholder="Filter by call ID..."
              value={filters.callId}
              onChange={(e) => setFilters((f) => ({ ...f, callId: e.target.value }))}
            />
          </div>
          <div className="filter-group filter-group-extension">
            <label>Source</label>
            <input
              type="text"
              placeholder="Filter by source..."
              value={filters.source}
              onChange={(e) => setFilters((f) => ({ ...f, source: e.target.value }))}
            />
          </div>
          <div className="filter-group filter-group-extension">
            <label>Destination</label>
            <input
              type="text"
              placeholder="Filter by destination..."
              value={filters.destination}
              onChange={(e) => setFilters((f) => ({ ...f, destination: e.target.value }))}
            />
          </div>
          <div className="filter-group filter-group-extension">
            <label>Front Desk Person</label>
            <input
              type="text"
              placeholder="Filter by front desk person..."
              value={filters.frontDeskPersonName}
              onChange={(e) => setFilters((f) => ({ ...f, frontDeskPersonName: e.target.value }))}
            />
          </div>
          <div className="filter-group filter-group-compact">
            <label>Processing Status</label>
            <select
              className="filter-date-input"
              value={filters.processingStatus}
              onChange={(e) => setFilters((f) => ({ ...f, processingStatus: e.target.value }))}
            >
              <option value="">All</option>
              <option value="Processed">Processed</option>
              <option value="Not Processed">Not Processed</option>
            </select>
          </div>
          <div className="filter-actions" style={{ display: "flex", flexDirection: "row", alignItems: "flex-end", gap: "10px" }}>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={clearAllFilters}
                className="btn-secondary filter-clear-btn"
                title="Clear all filters"
                style={{ whiteSpace: "nowrap" }}
              >
                Clear All Filters
              </button>
            )}
            <button
              onClick={exportToExcel}
              className="btn-export filter-export-btn"
              disabled={calls.length === 0}
              style={{ whiteSpace: "nowrap" }}
            >
              Export to Excel {calls.length > 0 ? `(${selectedRows.size})` : ""}
            </button>
          </div>
        </div>
      </div>

      {error && <div className="error-message">{error}</div>}

      <div className="table-wrapper">
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th className="col-checkbox" style={{ textAlign: "center" }}>
                  <input
                    type="checkbox"
                    className="row-checkbox"
                    checked={calls.length > 0 && selectedRows.size === calls.length}
                    onChange={toggleSelectAll}
                    aria-label="Select all rows"
                    title={selectedRows.size === calls.length ? "Unselect all" : "Select all"}
                  />
                </th>
                <th className="col-ref-id" style={{ textAlign: "center" }}>Call ID</th>
                <th>Date</th>
                <th>Time</th>
                <th>Src</th>
                <th>Dest</th>
                <th>Ext</th>
                <th>Dur</th>
                <th>Status</th>
                <th>Trans</th>
                <th>Audio</th>
                <th style={{ textAlign: "center" }}>AI</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((call, idx) => {
                const callRef = String(getField(call, "call_reference") || "");
                const analysisRow = resolveAnalysisRowForCall(call, analysisByRef);
                const isOpen = expandedRow === idx;
                return (
                <Fragment key={idx}>
                <tr className={selectedRows.has(idx) ? "selected" : "unselected"}>
                  <td className="col-checkbox" style={{ textAlign: "center" }}>
                    <input
                      type="checkbox"
                      className="row-checkbox"
                      checked={selectedRows.has(idx)}
                      onChange={() => toggleRow(idx)}
                      aria-label={`Select call ${getField(call, "formatted_call_id")}`}
                    />
                  </td>
                  <td className="col-ref-id" title={getField(call, "formatted_call_id")} onClick={() => setCellModal({ title: "Call ID", content: getField(call, "formatted_call_id") })} style={{ cursor: "pointer" }}>
                    <div className="ref-id-cell" style={{ justifyContent: "center" }}>
                      <span style={{ fontWeight: 400 }}>{getField(call, "formatted_call_id")}</span>
                    </div>
                  </td>
                  <td title={getField(call, "date", "call_date", "created_at")} onClick={() => setCellModal({ title: "Date", content: getField(call, "date", "call_date", "created_at") })} style={{ cursor: "pointer" }}>{getField(call, "date", "call_date", "created_at")}</td>
                  <td title={getField(call, "time", "call_time", "timestamp")} onClick={() => setCellModal({ title: "Time", content: getField(call, "time", "call_time", "timestamp") })} style={{ cursor: "pointer" }}>{getField(call, "time", "call_time", "timestamp")}</td>
                  <td title={getField(call, "src", "source", "from_number", "caller_id", "from")} onClick={() => setCellModal({ title: "Source", content: getField(call, "src", "source", "from_number", "caller_id", "from") })} style={{ cursor: "pointer" }}>{getField(call, "src", "source", "from_number", "caller_id", "from")}</td>
                  <td title={getField(call, "dst", "destination", "to_number", "dialed_number", "to")} onClick={() => setCellModal({ title: "Destination", content: getField(call, "dst", "destination", "to_number", "dialed_number", "to") })} style={{ cursor: "pointer" }}>{getField(call, "dst", "destination", "to_number", "dialed_number", "to")}</td>
                  <td title={getField(call, "extension", "ext")} onClick={() => setCellModal({ title: "Extension", content: getField(call, "extension", "ext") })} style={{ cursor: "pointer" }}><span className="ext-badge">{getField(call, "extension", "ext")}</span></td>
                  <td title={getField(call, "billsec", "duration", "call_duration")} onClick={() => setCellModal({ title: "Duration", content: getField(call, "billsec", "duration", "call_duration") })} style={{ cursor: "pointer" }}>{getField(call, "billsec", "duration", "call_duration")}</td>
                  <td title={getProcessingStatus(call)}>
                    <span className={`badge ${getProcessingStatus(call) === "Processed" ? "badge-complete" : "badge-na"}`}>
                      {getProcessingStatus(call)}
                    </span>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="transcript-view-btn"
                      onClick={async () => {
                        try { setTranscriptModalCall({ ...call, ...await getCallContent(call.call_id) }); }
                        catch (error) { setError(error.message || "Unable to load transcript"); }
                      }}
                      disabled={!call.call_id}
                      aria-label="View transcript"
                      title="View transcript"
                    >
                      <Eye size={16} strokeWidth={2.4} aria-hidden="true" />
                    </button>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="audio-play-btn"
                      onClick={() => handlePlayAudio(call)}
                      disabled={!getField(call, "audio_s3_uri", "audio_s3_key") || getField(call, "audio_s3_uri", "audio_s3_key") === "—"}
                      aria-label="Play audio"
                      title="Play audio"
                    >
                      <Play size={16} strokeWidth={2.4} aria-hidden="true" />
                    </button>
                  </td>
                  <td style={{ textAlign: "center" }}>
                    <button
                      type="button"
                      className="analysis-expand-btn"
                      onClick={() => toggleExpandedRow(idx)}
                      aria-label={isOpen ? "Collapse call analysis" : "Expand call analysis"}
                      title={isOpen ? "Collapse call analysis" : "Expand call analysis"}
                      style={{
                        padding: "6px 10px",
                        fontSize: "0.8rem",
                        fontWeight: 600,
                        borderRadius: "6px",
                        border: "1px solid #334155",
                        background: isOpen ? "linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)" : "transparent",
                        color: isOpen ? "#FFFFFF" : "#94A3B8",
                        cursor: "pointer",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "4px",
                      }}
                    >
                      {isOpen ? "Collapse" : "Expand"}
                      <ChevronDown
                        size={14}
                        strokeWidth={2.4}
                        aria-hidden="true"
                        style={{ transform: isOpen ? "rotate(180deg)" : "none", transition: "transform 0.15s ease" }}
                      />
                    </button>
                  </td>
                </tr>

                {isOpen && (
                  <tr className="analysis-expand-row">
                    <td colSpan="12" style={{ padding: 0, background: "#151d2c" }}>
                      <div className="analysis-card-body" style={{ padding: "14px 22px 22px" }}>
                        <div className="analysis-section">
                          <div className="section-badge section-a">
                            Call Analysis — {callRef || getField(call, "formatted_call_id")}
                          </div>
                          {analysisLoading && !analysisRow ? (
                            <div className="no-data" style={{ padding: "16px 0" }}>Loading analysis...</div>
                          ) : analysisRow ? (
                            <>
                              <p style={{ color: "#94a3b8", fontSize: "12px", marginBottom: "10px" }}>
                                Click any field to view its full value.
                              </p>
                              <div className="field-grid">
                                {analysisFields.map((field) => {
                                  const value = getAnalysisFieldValue(analysisRow, field.dbKey);
                                  const hasValue = value != null && value !== "";
                                  const content = hasValue ? formatAnalysisValue(value) : "—";
                                  const longValue = hasValue && isLongAnalysisValue(value);
                                  return (
                                    <button
                                      key={field.dbKey}
                                      type="button"
                                      className={`field-box${longValue ? " field-json" : ""}`}
                                      onClick={() => {
                                        setCellModal({
                                          title: field.name,
                                          content,
                                        });
                                      }}
                                      title={hasValue ? content : (field.notes || field.dbKey)}
                                      style={{
                                        textAlign: "left",
                                        width: "100%",
                                        border: "1px solid #334155",
                                        background: "#1b2434",
                                        cursor: "pointer",
                                        transition: "border-color 0.15s ease, background 0.15s ease",
                                      }}
                                    >
                                      <div className="field-label">{field.name}</div>
                                      <div className="field-value" style={{ color: hasValue ? "#cbd5e1" : "#64748b" }}>
                                        {content}
                                      </div>
                                    </button>
                                  );
                                })}
                              </div>
                            </>
                          ) : (
                            <div className="no-data" style={{ padding: "16px 0" }}>
                              No analysis found for {callRef || "this call"} yet.
                            </div>
                          )}
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
                </Fragment>
                );
              })}
              {calls.length === 0 && !loading && (
                <tr>
                  <td colSpan="12" className="no-data">
                      {allCalls.length > 0 && (filters.searchNumber || filters.extension || filters.callId || filters.source || filters.destination || filters.frontDeskPersonName || filters.processingStatus || filters.callDirection)
                      ? `No calls match ${[
                        filters.searchNumber && `number "${filters.searchNumber}"`,
                          filters.callDirection && `direction "${filters.callDirection}"`,
                          filters.extension && `extension "${filters.extension}"`,
                          filters.callId && `call ID "${filters.callId}"`,
                          filters.source && `source "${filters.source}"`,
                          filters.destination && `destination "${filters.destination}"`,
                          filters.frontDeskPersonName && `front desk person "${filters.frontDeskPersonName}"`,
                          filters.processingStatus && `status "${filters.processingStatus}"`,
                        ]
                          .filter(Boolean)
                          .join(" and ")}`
                      : error
                        ? ""
                        : "No records found."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {audioModalCall && (
        <div className="audio-modal-overlay" onClick={closeAudioModal}>
          <div className="audio-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="audio-modal-header">
              <div>
                <p className="audio-modal-kicker">Call Audio</p>
                <h2>{getField(audioModalCall, "call_reference")}</h2>
              </div>
              <div className="audio-modal-actions">
                <button
                  type="button"
                  className="audio-modal-download"
                  onClick={handleDownloadAudio}
                  disabled={audioLoading || audioDownloadLoading || !audioUrl}
                >
                  <Download size={16} strokeWidth={2.2} aria-hidden="true" />
                  {audioDownloadLoading ? "Preparing..." : "Download"}
                </button>
                <button type="button" className="audio-modal-close" onClick={closeAudioModal}>
                  Close
                </button>
              </div>
            </div>

            <div className="audio-modal-meta">
              <span>{getField(audioModalCall, "call_date")}</span>
              <span>{getField(audioModalCall, "call_time")}</span>
              <span>{getField(audioModalCall, "src", "source", "from_number", "caller_id", "from")}</span>
              <span>{getField(audioModalCall, "dst", "destination", "to_number", "dialed_number", "to")}</span>
            </div>

            {audioLoading && <div className="audio-modal-loading">Loading audio...</div>}
            {audioError && <div className="audio-modal-error">{audioError}</div>}
            {audioUrl && (
              <audio
                ref={audioRef}
                className="audio-modal-player"
                controls
                autoPlay
                playsInline
                preload="metadata"
                src={audioUrl}
                onError={() => setAudioError("The selected audio file could not be played.")}
              />
            )}
          </div>
        </div>
      )}

      {transcriptModalCall && (
        <div className="transcript-modal-overlay" onClick={closeTranscriptModal}>
          <div className="transcript-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="transcript-modal-header">
              <div>
                <p className="transcript-modal-kicker">Call Transcript</p>
                <h2>{getField(transcriptModalCall, "call_reference")}</h2>
              </div>
              <div className="transcript-modal-actions">
                <button
                  type="button"
                  className="transcript-modal-copy"
                  onClick={() => handleCopyTranscript(getField(transcriptModalCall, "transcript"))}
                >
                  {copiedTranscript ? (
                    <>
                      <Check size={16} strokeWidth={2.2} aria-hidden="true" />
                      Copied!
                    </>
                  ) : (
                    <>
                      <Copy size={16} strokeWidth={2.2} aria-hidden="true" />
                      Copy
                    </>
                  )}
                </button>
                <button type="button" className="transcript-modal-close" onClick={closeTranscriptModal}>
                  Close
                </button>
              </div>
            </div>

            <div className="transcript-modal-meta">
              <span>{getField(transcriptModalCall, "call_date")}</span>
              <span>{getField(transcriptModalCall, "call_time")}</span>
              <span>{getField(transcriptModalCall, "src", "source", "from_number", "caller_id", "from")}</span>
              <span>{getField(transcriptModalCall, "dst", "destination", "to_number", "dialed_number", "to")}</span>
            </div>

            <div className="transcript-modal-content">
              <p>{getField(transcriptModalCall, "transcript")}</p>
            </div>
          </div>
        </div>
      )}

      {cellModal && (
        <div className="audio-modal-overlay" onClick={closeCellModal}>
          <div className="audio-modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "600px" }}>
            <div className="audio-modal-header">
              <div>
                <p className="audio-modal-kicker">Field Details</p>
                <h2>{cellModal.title}</h2>
              </div>
              <div className="audio-modal-actions">
                <button type="button" className="audio-modal-close" onClick={closeCellModal}>
                  <X size={18} strokeWidth={2.2} />
                </button>
              </div>
            </div>
            <div className="audio-modal-content" style={{ padding: "20px", maxHeight: "60vh", overflowY: "auto", fontSize: "14px", lineHeight: "1.6", color: "#e2e8f0", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
              {cellModal.content}
            </div>
          </div>
        </div>
      )}

      <BufferingOverlay loading={loading} text="Fetching call records..." />
    </div>
  );
}

export default CallRecords;
