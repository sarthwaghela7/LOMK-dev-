export function parseLeadDate(value) {
  const raw = String(value || "").trim();
  if (!raw) return null;
  const usDate = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  const isoDate = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  const parts = usDate
    ? [Number(usDate[3]), Number(usDate[1]), Number(usDate[2])]
    : isoDate
      ? [Number(isoDate[1]), Number(isoDate[2]), Number(isoDate[3])]
      : null;
  if (!parts) return null;
  const [year, month, day] = parts;
  const parsed = new Date(year, month - 1, day);
  if (parsed.getFullYear() !== year || parsed.getMonth() !== month - 1 || parsed.getDate() !== day) return null;
  return parsed;
}
