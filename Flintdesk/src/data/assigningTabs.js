// The tabs of the consolidated "Assigning the Calls" page, and how the old pages / notifications map onto them.
export const ASSIGNING_TABS = [
  { key: "leads", label: "Leads" },
  { key: "follow-ups", label: "Follow Ups" },
  { key: "sm-leads", label: "SM Leads" },
  { key: "general", label: "Call Tasks" },
  { key: "missed-calls", label: "Missed Calls" },
];

// The page and sidebar name: an agent only receives calls here, so for them it is "Assigned Calls".
export const assigningPageTitle = (restrictedAgent) => (restrictedAgent ? "Assigned Calls" : "Assigning the Calls");

export const DEFAULT_ASSIGNING_TAB = "leads";
export const ASSIGNING_PAGE = "assigning-calls";
const TAB_KEYS = new Set(ASSIGNING_TABS.map((tab) => tab.key));

export const validAssigningTab = (value) => (TAB_KEYS.has(value) ? value : DEFAULT_ASSIGNING_TAB);

// Pages that used to be sidebar entries of their own (also what an old saved "last page" can hold).
const LEGACY_PAGE_TABS = { leads: "leads", tasks: "general", "sm-leads": "sm-leads", "missed-calls": "missed-calls" };
export const legacyPageTab = (page) => LEGACY_PAGE_TABS[page] || "";

// Which tab a notification belongs to; undefined for notifications that are not assignments.
export function tabForNotification(notification) {
  const type = String(notification?.notification_type || "");
  if (type === "lead_assignment") return "leads";
  if (type === "sm_lead_assignment") return "sm-leads";
  if (type === "missed_call_assignment") return "missed-calls";
  if (type === "follow_up_assignment" || type === "follow_up_unassigned") return "follow-ups";
  if (type === "non_responsiveness_assignment") return "general";
  if (String(notification?.source_page || "") === "tasks" || type.startsWith("task")) return "general";
  return undefined;
}

// The number on a tab's badge: nothing at zero, "99+" beyond two digits.
export const badgeText = (count) => {
  const value = Number(count) || 0;
  return value <= 0 ? "" : value > 99 ? "99+" : String(value);
};
