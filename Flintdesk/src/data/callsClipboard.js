// Copy-to-spreadsheet for the Calls page: a header row followed by the rows, tab-separated.
//
// Two clipboard flavors are written together:
//   * text/plain - header + rows, real TAB between columns and "\n" between rows (any text box, Notepad).
//   * text/html  - the same table, with identifier-like columns (phone numbers, IDs, times) marked as text so
//                  Excel and Google Sheets keep "+19133886745", "0123" or "3:01" exactly as shown instead of
//                  turning them into numbers or times.

// Columns whose displayed value must stay text when pasted into a spreadsheet.
export const TEXT_FORMAT_KEYS = new Set([
  "id", "clientId", "leadId", "clientPhoneNumber", "capturedNumber", "extension", "timestamp", "duration",
]);

// One cell, made safe for a tab/newline separated paste:
//  - tabs and line breaks inside the value would shift data into other columns/rows, so each run becomes a space;
//  - a value starting with "=" or "@" would be executed as a formula by Excel/Sheets, so it is marked as text.
export function sanitizeCell(value) {
  const text = String(value ?? "").replace(/[\t\r\n\u2028\u2029]+/g, " ").trim();
  return /^[=@]/.test(text) ? `'${text}` : text;
}

// columns: [{ key, label }], rowValues: one array per call, in column order.
// formatters: optional { [columnKey]: (value) => displayedValue } for columns shown differently than stored.
export function buildCopyTable(columns, rowValues, formatters = {}) {
  const headers = columns.map((column) => sanitizeCell(column.label ?? column.key));
  const rows = rowValues.map((values) =>
    // Exactly one cell per column, so an empty or missing value can never pull the next one left.
    columns.map((column, index) => {
      const raw = values?.[index];
      const format = formatters[column.key];
      return sanitizeCell(format ? format(raw) : raw);
    }));
  return { headers, rows, columns };
}

export const toTabSeparated = (table) => [table.headers, ...table.rows].map((cells) => cells.join("\t")).join("\n");

const escapeHtml = (text) => text
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

export function toClipboardHtml(table, textKeys = TEXT_FORMAT_KEYS) {
  const cell = (value, asText) => {
    const shown = escapeHtml(value);
    if (!asText) return `<td>${shown}</td>`;
    // mso-number-format: Excel keeps it as text. data-sheets-value type 2: Google Sheets keeps it as a string.
    const sheets = escapeHtml(JSON.stringify({ 1: 2, 2: value }));
    return `<td style="mso-number-format:'\\@'" data-sheets-value="${sheets}">${shown}</td>`;
  };
  const line = (cells) => `<tr>${cells.map((value, index) => cell(value, textKeys.has(table.columns[index]?.key))).join("")}</tr>`;
  const header = `<tr>${table.headers.map((value) => `<td>${escapeHtml(value)}</td>`).join("")}</tr>`;
  return `<table><tbody>${header}${table.rows.map(line).join("")}</tbody></table>`;
}

// Resolves only when the browser really accepted the data; rejects otherwise, so the caller never shows a
// false "Copied". Returns "rich" (text + html) or "plain" (text only fallback).
export async function writeTableToClipboard(table, nav = globalThis.navigator, ClipboardItemCtor = globalThis.ClipboardItem) {
  const clipboard = nav?.clipboard;
  if (!clipboard) throw new Error("Clipboard is not available in this browser context.");
  const text = toTabSeparated(table);
  if (typeof clipboard.write === "function" && ClipboardItemCtor) {
    try {
      await clipboard.write([new ClipboardItemCtor({
        "text/plain": new Blob([text], { type: "text/plain" }),
        "text/html": new Blob([toClipboardHtml(table)], { type: "text/html" }),
      })]);
      return "rich";
    } catch {
      // Some browsers refuse the multi-format write; try plain text before giving up.
    }
  }
  if (typeof clipboard.writeText !== "function") throw new Error("Clipboard is not available in this browser context.");
  await clipboard.writeText(text);
  return "plain";
}
