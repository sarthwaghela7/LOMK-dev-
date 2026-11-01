// A generic table -> PDF (landscape A4) for exports of wide tables. Nothing is cut off: long text wraps inside its cell, a row that does
// not fit the page moves to the next page (header repeated), and a table wider than a page is printed in column parts, each part
// repeating the first `repeatColumns` columns so every row can still be identified. Pure layout helpers are exported for tests.

const MARGIN = 10;
const MIN_COL = 18;
const MAX_COL = 62;
const LINE_MM = 3.5;
const PAD = 1.6;

// Width in mm a column needs: its heading and its longest value (capped), clamped to a readable range.
export function columnWidths(columns, rows) {
  return columns.map((label, index) => {
    const longest = rows.reduce((max, row) => Math.max(max, String(row[index] ?? "").split(/\r?\n/).reduce((m, l) => Math.max(m, l.length), 0)), 0);
    const chars = Math.min(Math.max(String(label).length * 0.8, Math.min(longest, 46)), 46);
    // A heading is wrapped between words only: the column is at least as wide as the heading's longest word (bold, so wider per letter).
    const longestWord = String(label).split(/\s+/).reduce((max, word) => Math.max(max, word.length), 0);
    return Math.max(MIN_COL, Math.round(longestWord * 2.1 + PAD * 2 + 1), Math.min(MAX_COL, Math.round(chars * 1.9 + PAD * 2)));
  });
}

// Split the column indexes into parts that each fit `available` mm; the first `repeat` columns open every part.
export function columnParts(widths, available, repeat = 1) {
  const fixed = widths.slice(0, repeat).reduce((sum, width) => sum + width, 0);
  const parts = [];
  let current = [];
  let used = fixed;
  for (let index = repeat; index < widths.length; index += 1) {
    if (current.length && used + widths[index] > available) { parts.push(current); current = []; used = fixed; }
    current.push(index);
    used += widths[index];
  }
  if (current.length || !parts.length) parts.push(current);
  return parts.map((indexes) => [...Array.from({ length: repeat }, (_, i) => i), ...indexes]);
}

export async function exportTablePdf({ title, subtitle = "", columns, rows, filename, repeatColumns = 1 }) {
  const { jsPDF } = await import("jspdf");
  const pdf = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4" });
  const pageWidth = pdf.internal.pageSize.getWidth();
  const pageHeight = pdf.internal.pageSize.getHeight();
  const available = pageWidth - MARGIN * 2;
  const widths = columnWidths(columns, rows);
  const parts = columnParts(widths, available, Math.min(repeatColumns, columns.length));
  const exported = new Date().toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
  let firstPage = true;

  parts.forEach((indexes, partIndex) => {
    const colWidths = indexes.map((index) => widths[index]);
    const headers = indexes.map((index) => columns[index]);
    let y = 0;
    const header = (continued) => {
      pdf.setFont("helvetica", "bold").setFontSize(continued ? 11 : 14).setTextColor(20);
      pdf.text(`${title}${continued ? " (continued)" : ""}`, MARGIN, y + 4);
      pdf.setFont("helvetica", "normal").setFontSize(8).setTextColor(90);
      const detail = [subtitle, `Exported ${exported}`, `${rows.length} record${rows.length === 1 ? "" : "s"}`, parts.length > 1 ? `Columns part ${partIndex + 1} of ${parts.length}` : ""].filter(Boolean).join("  |  ");
      pdf.text(detail, MARGIN, y + 9);
      y += 12;
      pdf.setFont("helvetica", "bold").setFontSize(7.5).setTextColor(20);
      const lines = headers.map((label, i) => pdf.splitTextToSize(String(label), colWidths[i] - PAD * 2));
      const height = Math.max(...lines.map((l) => l.length)) * LINE_MM + PAD * 2;
      let x = MARGIN;
      lines.forEach((cellLines, i) => {
        pdf.setFillColor(226, 238, 250).rect(x, y, colWidths[i], height, "F");
        pdf.setDrawColor(150).rect(x, y, colWidths[i], height);
        pdf.text(cellLines, x + PAD, y + PAD + 2.4);
        x += colWidths[i];
      });
      y += height;
    };
    const startPage = (continued) => {
      if (!firstPage) pdf.addPage();
      firstPage = false;
      y = MARGIN;
      header(continued);
    };
    startPage(false);
    if (!rows.length) {
      pdf.setFont("helvetica", "normal").setFontSize(9).setTextColor(90).text("No records match the current filters.", MARGIN, y + 8);
    }
    rows.forEach((row, rowIndex) => {
      pdf.setFont("helvetica", "normal").setFontSize(7.5).setTextColor(20);
      const cells = indexes.map((index, i) => pdf.splitTextToSize(String(row[index] ?? ""), colWidths[i] - PAD * 2));
      const height = Math.min(Math.max(...cells.map((l) => l.length), 1) * LINE_MM + PAD * 2, pageHeight - MARGIN * 2 - 30);
      if (y + height > pageHeight - MARGIN - 6) startPage(true);
      let x = MARGIN;
      if (rowIndex % 2) pdf.setFillColor(248, 250, 252).rect(MARGIN, y, colWidths.reduce((a, b) => a + b, 0), height, "F");
      cells.forEach((cellLines, i) => {
        pdf.setDrawColor(190).rect(x, y, colWidths[i], height);
        pdf.setFont("helvetica", "normal").setFontSize(7.5).setTextColor(20);
        pdf.text(cellLines, x + PAD, y + PAD + 2.4);
        x += colWidths[i];
      });
      y += height;
    });
  });

  const total = pdf.getNumberOfPages();
  for (let page = 1; page <= total; page += 1) {
    pdf.setPage(page);
    pdf.setFont("helvetica", "normal").setFontSize(7.5).setTextColor(120);
    pdf.text(`Page ${page} of ${total}`, pageWidth - MARGIN, pageHeight - 5, { align: "right" });
  }
  pdf.save(filename);
}
