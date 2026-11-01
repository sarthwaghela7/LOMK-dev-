// Shared import parsing and header matching for spreadsheet, CSV, and TXT imports.
export const normalizeImportHeader = (value) => String(value || "")
  .trim()
  .toLowerCase()
  .replace(/[_-]+/g, " ")
  .replace(/\s+/g, " ")
  .replace(/[^a-z0-9]/g, "");

export function parseDelimitedTable(text, delimiter) {
  const lines = String(text || "").replace(/^\uFEFF/, "").split(/\r?\n/).filter((line) => line.trim());
  if (!lines.length) return { headers: [], rows: [] };
  const split = (line) => {
    const cells = []; let cell = ""; let quoted = false;
    for (let index = 0; index < line.length; index += 1) {
      const char = line[index];
      if (char === '"') { if (quoted && line[index + 1] === '"') { cell += char; index += 1; } else quoted = !quoted; }
      else if (char === delimiter && !quoted) { cells.push(cell.trim()); cell = ""; }
      else cell += char;
    }
    cells.push(cell.trim());
    return cells;
  };
  return { headers: split(lines[0]), rows: lines.slice(1).map(split) };
}

function isMarkdownSeparatorRow(cells) {
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(String(cell).trim()));
}

export function parseMarkdownTable(text) {
  const lines = String(text || "").replace(/^\uFEFF/, "").split(/\r?\n/).filter((line) => line.trim());
  const split = (line) => {
    const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
    const cells = []; let cell = ""; let quoted = false;
    for (let index = 0; index < trimmed.length; index += 1) {
      const char = trimmed[index];
      if (char === '"') { if (quoted && trimmed[index + 1] === '"') { cell += char; index += 1; } else quoted = !quoted; }
      else if (char === "\\" && trimmed[index + 1] === "|") { cell += "|"; index += 1; }
      else if (char === "|" && !quoted) { cells.push(cell.trim()); cell = ""; }
      else cell += char;
    }
    cells.push(cell.trim());
    return cells;
  };
  const tableRows = lines.map(split).filter((cells) => !isMarkdownSeparatorRow(cells));
  return { headers: tableRows[0] || [], rows: tableRows.slice(1) };
}

// Paste accepts the same spreadsheet data as file imports, plus Markdown
// tables copied from docs/chat. Header matching remains the common layer.
export function parsePastedTable(text) {
  const value = String(text || "");
  const firstMeaningfulLine = value.split(/\r?\n/).find((line) => line.trim()) || "";
  if (firstMeaningfulLine.includes("\t")) return parseDelimitedTable(value, "\t");
  if (/^\s*\|.*\|\s*$/.test(firstMeaningfulLine)) return parseMarkdownTable(value);
  if (firstMeaningfulLine.includes(",")) return parseDelimitedTable(value, ",");
  return parseDelimitedTable(value, "\t");
}

// Fields are supplied by each page's own table/schema configuration.  A header is
// mapped only when it identifies one destination field and that field appears once.
export function mapImportHeaders(headers, fields, aliases = {}) {
  const destinations = new Map();
  fields.forEach(([key, label]) => {
    const normalized = normalizeImportHeader(label);
    if (!destinations.has(normalized)) destinations.set(normalized, []);
    destinations.get(normalized).push(key);
  });
  Object.entries(aliases).forEach(([header, key]) => {
    const normalized = normalizeImportHeader(header);
    if (!destinations.has(normalized)) destinations.set(normalized, []);
    if (!destinations.get(normalized).includes(key)) destinations.get(normalized).push(key);
  });

  const candidates = headers.map((header) => destinations.get(normalizeImportHeader(header)) || []);
  const counts = new Map();
  candidates.forEach((keys) => { if (keys.length === 1) counts.set(keys[0], (counts.get(keys[0]) || 0) + 1); });
  const mapping = candidates.map((keys) => keys.length === 1 && counts.get(keys[0]) === 1 ? keys[0] : "");
  const problems = headers.flatMap((header, index) => {
    // Unmatched headers are deliberately ignored: partial imports are valid.
    if (!String(header || "").trim()) return [];
    if (candidates[index].length > 1) return [`"${header}" matches more than one destination column.`];
    if (!candidates[index].length) return [];
    if (!mapping[index]) return [`"${header}" duplicates another column mapped to ${candidates[index][0]}.`];
    return [];
  });
  const unmatchedHeaders = headers.filter((header, index) => String(header || "").trim() && !candidates[index].length);
  return { mapping, problems, unmatchedHeaders };
}

export function recordsFromImportTable({ headers, rows, mapping }) {
  return rows.map((cells) => Object.fromEntries(headers.map((_, index) => [mapping[index], cells[index]]).filter(([key]) => key)));
}
