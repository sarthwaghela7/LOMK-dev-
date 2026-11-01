export const LEAD_SOURCE_GROUPS = Object.freeze([
  Object.freeze({ label: "Organic Leads", options: Object.freeze(["Client Referral", "Lomk Seminars", "Friends", "Family", "Tamil Sangam", "Paid Consultation", "Google My Business"]) }),
  Object.freeze({ label: "Paid Leads", options: Object.freeze(["Google Search", "Youtube", "Instagram", "Website", "Email", "Ads/Brevo"]) }),
]);

// Keep legacy saved spellings selectable while new choices persist the agreed
// leaf values; this avoids changing historical lead-source records.
export const LEAD_SOURCE_OPTIONS = Object.freeze([
  ...LEAD_SOURCE_GROUPS.flatMap((group) => group.options),
  "LOMK seminar/workshops",
  "Paid Consultation Client",
]);
