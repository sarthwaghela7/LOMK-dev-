const escapeXml = (value) => String(value ?? "")
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;")
  .replace(/'/g, "&apos;");

const cell = (value, styleId = "") => {
  // A real number is written as a number so Excel can add it up; every string (ids, phone numbers, text) stays text.
  if (typeof value === "number" && Number.isFinite(value)) {
    return `<Cell${styleId ? ` ss:StyleID="${styleId}"` : ""}><Data ss:Type="Number">${value}</Data></Cell>`;
  }
  const text = String(value ?? "");
  const date = /^\d{4}-\d{2}-\d{2}(?:T.*)?$/.test(text);
  // A date carries a date format (Excel would otherwise show its serial number).
  const style = styleId || (date ? "Date" : "");
  return `<Cell${style ? ` ss:StyleID="${style}"` : ""}><Data ss:Type="${date ? "DateTime" : "String"}">${escapeXml(date ? `${text.slice(0, 10)}T00:00:00.000` : text)}</Data></Cell>`;
};

const normalizeColumn = (column) =>
  typeof column === "string" ? { label: column } : column;

const columnWidth = (column, rows, index) => {
  const headingLength = String(column.label ?? column.key ?? "").length;
  const longestValue = rows.reduce(
    (longest, row) => Math.max(longest, String(row[index] ?? "").length),
    headingLength,
  );
  // SpreadsheetML width is measured in points. Cap text fields so one long
  // note cannot make unrelated columns unusably wide; WrapText preserves it.
  const maximum = column.maxWidth ?? (column.wrap ? 320 : 210);
  const minimum = column.minWidth ?? 62;
  return Math.min(maximum, Math.max(minimum, Math.ceil(longestValue * 6.6 + 16)));
};

export function downloadExcelWorkbook(sheets, filename) {
  const worksheets = sheets.map(({ name, columns, rows }) => {
    const normalizedColumns = columns.map(normalizeColumn);
    return `
    <Worksheet ss:Name="${escapeXml(name.slice(0, 31))}">
      <Table>
        ${normalizedColumns.map((column, index) => `<Column ss:AutoFitWidth="0" ss:Width="${column.width ?? columnWidth(column, rows, index)}"/>`).join("")}
        <Row ss:AutoFitHeight="1">${normalizedColumns.map((column) => cell(column.label ?? column.key ?? "", column.headerStyle || "Header")).join("")}</Row>
        ${rows.map((row) => `<Row ss:AutoFitHeight="1">${row.map((value, index) => cell(value, normalizedColumns[index]?.wrap ? "Wrapped" : "")).join("")}</Row>`).join("")}
      </Table>
      <WorksheetOptions xmlns="urn:schemas-microsoft-com:office:excel">
        <FreezePanes/><FrozenNoSplit/><SplitHorizontal>1</SplitHorizontal><TopRowBottomPane>1</TopRowBottomPane>
        <AutoFilter x:Range="R1C1:R${Math.max(1, rows.length + 1)}C${Math.max(1, normalizedColumns.length)}"/>
      </WorksheetOptions>
    </Worksheet>`;
  }).join("");
  const workbook = `<?xml version="1.0"?><?mso-application progid="Excel.Sheet"?>
    <Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
      <Styles>
        <Style ss:ID="Default" ss:Name="Normal"><Alignment ss:Vertical="Center"/><Font ss:FontName="Aptos" ss:Size="10"/></Style>
        <Style ss:ID="Header"><Alignment ss:Vertical="Center" ss:WrapText="1"/><Font ss:FontName="Aptos" ss:Size="10" ss:Bold="1"/><Interior ss:Color="#F3F4F6" ss:Pattern="Solid"/></Style>
        <Style ss:ID="Date"><Alignment ss:Vertical="Center" ss:Horizontal="Left"/><NumberFormat ss:Format="yyyy-mm-dd"/></Style>
        <Style ss:ID="Wrapped"><Alignment ss:Vertical="Top" ss:WrapText="1"/></Style>
      </Styles>
      ${worksheets}
    </Workbook>`;
  const blob = new Blob([workbook], { type: "application/vnd.ms-excel" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
