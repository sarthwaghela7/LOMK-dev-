import { LEAD_SOURCE_OPTIONS } from "./leadSource.js";

// Canonical dropdown values shared by the Calls page and Dashboard category
// tables. Keep these lists here so dashboard columns cannot drift from Calls.
export const CLIENT_TYPE_OPTIONS = Object.freeze([
  "New Client - Individual",
  "New Client - Organization",
  "Existing client - Individual",
  "Existing client - Organization",
  "Voicemail Call",
  "Spam / Wrong Call",
]);

export const URGENCY_FLAG_OPTIONS = Object.freeze([
  "None",
  "Urgent - RFE",
  "Urgent - NOID",
  "Grace Period",
  "Visa Expiry",
  "Court Date",
  "Filing Deadline",
  "Client Distressed",
]);

export const LEAD_SOURCE_DROPDOWN_OPTIONS = LEAD_SOURCE_OPTIONS;
