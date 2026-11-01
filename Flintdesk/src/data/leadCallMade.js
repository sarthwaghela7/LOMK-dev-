// S1 / S2 Leads rows: has the lead's call been made, and on which day? (the backend works this out in Central time)
//   outbound_call_id          first outbound call made on/after the lead's assigned (import) date, any day
//   outbound_call_date        that call's date (YYYY-MM-DD)
//   outbound_call_same_day    that call was made on the assigned date itself
//   same_day_outbound_call_id (older field, still sent) the call made on the assigned date
import { formatDisplayDate } from "./displayDate.js";

// The S1 row is green once a call has been made - the same day or any later day.
export const leadCallMade = (row) => Boolean(row?.outbound_call_id || row?.same_day_outbound_call_id);

// Shown under the phone number when the call was NOT made on the assigned date: "Call made on 10/13/2026" (MM/DD/YYYY).
export function leadCallMadeOnNote(row) {
  if (!row?.outbound_call_id || row.outbound_call_same_day || !row.outbound_call_date) return "";
  const date = formatDisplayDate(row.outbound_call_date, "");
  return date ? `Call made on ${date}` : "";
}
