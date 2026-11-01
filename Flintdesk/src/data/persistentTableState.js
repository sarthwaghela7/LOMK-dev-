import { useEffect, useState } from "react";
import { businessDateCT } from "./filterDates.js";

export function tableStorageKey(key) {
  try {
    return `flintdesk_table:${JSON.parse(localStorage.getItem("flintdesk_user") || "null")?.id || "anonymous"}:${key}`;
  } catch { return `flintdesk_table:anonymous:${key}`; }
}

// A saved date range belongs to the business day (Central Time) it was saved on: a tab left open past midnight, or a range from
// an earlier day, must not keep showing old data. The day is stored next to the value; a value saved before this existed
// (no day stored) is treated as current.
const DATE = /^\d{4}-\d{2}-\d{2}$/;
export const looksLikeDateRange = (value) => Boolean(value) && typeof value === "object" && DATE.test(String(value.start || "")) && DATE.test(String(value.end || ""));
export const isFromAnEarlierDay = (savedDay, now = new Date()) => Boolean(savedDay) && savedDay !== businessDateCT(now);
const dayKey = (storageKey) => `${storageKey}:day`;
export function savedOnAnEarlierDay(storageKey) {
  try { return isFromAnEarlierDay(sessionStorage.getItem(dayKey(storageKey))); } catch { return false; }
}
export function markSavedToday(storageKey) {
  try { sessionStorage.setItem(dayKey(storageKey), businessDateCT()); } catch { /* Storage is optional. */ }
}

// Persist controls only. Saved rows and manual values always come from the API.
export function usePersistentTableState(key, initial) {
  const storageKey = tableStorageKey(key);
  const [value, setValue] = useState(() => {
    const fallback = typeof initial === "function" ? initial() : initial;
    try {
      const raw = sessionStorage.getItem(storageKey);
      if (!raw) return fallback;
      const saved = JSON.parse(raw);
      if (saved === null || typeof saved !== typeof fallback || Array.isArray(saved) !== Array.isArray(fallback)) return fallback;
      if (looksLikeDateRange(saved) && savedOnAnEarlierDay(storageKey)) return fallback;      // yesterday's range: back to the default day
      return typeof fallback === "object" && !Array.isArray(fallback) ? { ...fallback, ...saved } : saved;
    } catch { return fallback; }
  });
  useEffect(() => {
    try { sessionStorage.setItem(storageKey, JSON.stringify(value)); } catch { /* Storage is optional. */ }
    markSavedToday(storageKey);
  }, [storageKey, value]);
  return [value, setValue];
}
