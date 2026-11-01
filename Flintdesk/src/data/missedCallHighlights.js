import { normalizeRoutingNumber } from "./leadNumberFilter.js";

// ---- Pending / Completed / Same Client Number ------------------------------

/**
 * The one definition of a "green" (attended) Missed Calls row: a matching
 * outbound callback exists. Hannah AI rows carry the lightweight
 * `callback_matched` flag, Called Back rows the full `callback` object.
 * Row colouring, Pending and Completed all use this.
 */
export const isAttendedMissedCall = (row) => Boolean(row?.data?.callback || row?.data?.callback_matched);

// Same normalisation the rest of the app uses for phone numbers (strip
// punctuation, drop a leading US country code 1). Numbers too short to
// identify a client (blank, "Anonymous", an extension) never form a group.
const MIN_CLIENT_NUMBER_DIGITS = 7;
export const missedCallClientNumber = (row) => {
  const number = normalizeRoutingNumber(row?.data?.contact_number);
  return number.length >= MIN_CLIENT_NUMBER_DIGITS ? number : "";
};

/**
 * Client numbers appearing on more than one row of `rows`, in one pass.
 * `rows` is the table's data with every other filter already applied, so the
 * result follows the filter belt.
 */
export const repeatedClientNumbers = (rows) => {
  const counts = new Map();
  for (const row of rows) {
    const number = missedCallClientNumber(row);
    if (number) counts.set(number, (counts.get(number) || 0) + 1);
  }
  const repeated = new Set();
  counts.forEach((count, number) => { if (count > 1) repeated.add(number); });
  return repeated;
};

export const MISSED_CALL_STATUS_FILTERS = Object.freeze({
  pending: "Pending",
  completed: "Completed",
  same_client: "Same Client Number",
});

/** Apply the Pending / Completed / Same Client Number button to filtered rows. */
export const filterMissedCallsByStatus = (rows, statusFilter, repeatedNumbers) => {
  if (statusFilter === "pending") return rows.filter((row) => !isAttendedMissedCall(row));
  if (statusFilter === "completed") return rows.filter(isAttendedMissedCall);
  if (statusFilter === "same_client") return rows.filter((row) => repeatedNumbers.has(missedCallClientNumber(row)));
  return rows;
};
