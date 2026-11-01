function parseDateParts(value) {
  const match = String(value ?? "")
    .trim()
    .match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (match) return match.slice(1).map(Number);
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return [parsed.getFullYear(), parsed.getMonth() + 1, parsed.getDate()];
}

export function formatDisplayDate(value, fallback = "-") {
  if (value === null || value === undefined || String(value).trim() === "")
    return fallback;
  const parts = parseDateParts(value);
  if (!parts) return String(value);
  const [year, month, day] = parts;
  return `${String(month).padStart(2, "0")}/${String(day).padStart(2, "0")}/${String(year).padStart(4, "0")}`;
}

export function formatDisplayDateTime(value, fallback = "-") {
  if (value === null || value === undefined || String(value).trim() === "")
    return fallback;
  const text = String(value).trim();
  const formatted = formatDisplayDate(text, fallback);
  const timeMatch = text.match(/[T ](\d{2}:\d{2}(?::\d{2})?)/);
  return timeMatch ? `${formatted} ${timeMatch[1]}` : formatted;
}
