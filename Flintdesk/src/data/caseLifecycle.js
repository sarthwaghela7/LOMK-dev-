// Case lifecycle stages (the backend classifies every call record: AutoFlint_Backend/services/case_lifecycle.py).
// A call's stage is its own: client number, context and lead never group calls here.
export const LIFECYCLE_STAGES = [
  { key: "open", label: "Open Cases" },
  { key: "in_transition", label: "In-Transition Cases" },
  { key: "lost", label: "Lost Cases" },
  { key: "dropped", label: "Dropped Cases" },
  { key: "success", label: "Success Cases" },
];

const KEYS = new Set(LIFECYCLE_STAGES.map((stage) => stage.key));

// A restored / unknown value falls back to "no lifecycle filter" instead of silently hiding calls.
export const validLifecycleStage = (value) => (KEYS.has(value) ? value : "");

// One stage at a time: choosing the selected stage again clears it; choosing another replaces it.
export const nextLifecycleSelection = (current, clicked) => (current === clicked ? "" : validLifecycleStage(clicked));

// Dashboard cards: [{key, label, value}] in the fixed order, from the API's {counts, total}.
export function lifecycleCards(payload) {
  const counts = payload?.counts || {};
  return LIFECYCLE_STAGES.map(({ key, label }) => ({ key, label, value: Number.isFinite(Number(counts[key])) ? Number(counts[key]) : 0 }));
}
