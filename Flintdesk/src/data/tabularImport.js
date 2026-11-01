const countDelimitersOutsideQuotes = (value, delimiter) => {
  let quoted = false;
  let count = 0;
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if (char === '"') {
      if (quoted && value[index + 1] === '"') index += 1;
      else quoted = !quoted;
    } else if (char === delimiter && !quoted) count += 1;
  }
  return count;
};

const hasUnclosedQuote = (value) => {
  let quoted = false;
  for (let index = 0; index < value.length; index += 1) {
    if (value[index] !== '"') continue;
    if (quoted && value[index + 1] === '"') index += 1;
    else quoted = !quoted;
  }
  return quoted;
};

export function parseDelimitedRow(value, delimiter) {
  const cells = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if (char === '"') {
      if (quoted && value[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else quoted = !quoted;
    } else if (char === delimiter && !quoted) {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += char;
    }
  }
  cells.push(cell.trim());
  return cells;
}

const normalizedHeader = (value) =>
  String(value || "").toLowerCase().replace(/[^a-z0-9]/g, "");

const isLeadHeader = (cells) => {
  const headers = new Set(cells.map(normalizedHeader));
  return headers.has("phone") && (headers.has("leadidassigned") || headers.has("leadid"));
};

export const parseQuotedDelimitedText = (text, delimiter) => {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === '"') {
      if (quoted && text[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else quoted = !quoted;
    } else if (char === delimiter && !quoted) {
      row.push(cell.trim());
      cell = "";
    } else if (char === "\n" && !quoted) {
      row.push(cell.trim());
      if (row.some((value) => value)) rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += char;
    }
  }
  row.push(cell.trim());
  if (row.some((value) => value)) rows.push(row);
  return rows;
};

/** Parse spreadsheet clipboard text without splitting multiline cells into rows. */
export function parseTable(text, delimiter = "\t") {
  const normalizedText = String(text || "")
    .replace(/^\uFEFF/, "")
    .replace(/\r\n?/g, "\n");

  // RFC-style CSV has explicit quoting, so parse the complete document first
  // and find the first actual tracker header rather than trusting line one.
  if (delimiter === ",") {
    const logicalRows = parseQuotedDelimitedText(normalizedText, delimiter);
    const headerIndex = logicalRows.findIndex(isLeadHeader);
    if (headerIndex < 0) return { headers: [], rows: [], headerError: "A lead tracker header containing Phone and Lead ID Assigned was not found." };
    return { headers: logicalRows[headerIndex], rows: logicalRows.slice(headerIndex + 1) };
  }

  const physicalLines = normalizedText.split("\n");
  const headerIndex = physicalLines.findIndex((line) => isLeadHeader(parseDelimitedRow(line, delimiter)));
  if (headerIndex < 0) return { headers: [], rows: [], headerError: "A lead tracker header containing Phone and Lead ID Assigned was not found." };
  const headers = parseDelimitedRow(physicalLines[headerIndex], delimiter);
  const expectedSeparators = Math.max(0, headers.length - 1);
  const records = [];
  let pending = "";

  for (const line of physicalLines.slice(headerIndex + 1)) {
    pending = pending ? `${pending}\n${line}` : line;
    // Excel/Sheets normally quote multiline cells. Some clipboard providers
    // do not: for TSV, accumulate physical lines until the header's complete
    // column shape is present, retaining the newline in the active cell.
    if (hasUnclosedQuote(pending)) continue;
    if (delimiter === "\t" && countDelimitersOutsideQuotes(pending, delimiter) < expectedSeparators) continue;
    if (pending.trim()) records.push(parseDelimitedRow(pending, delimiter));
    pending = "";
  }
  if (pending.trim()) records.push(parseDelimitedRow(pending, delimiter));
  return { headers, rows: records };
}
