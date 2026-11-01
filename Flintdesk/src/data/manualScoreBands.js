// Fixed quality bands for the Manual Data Score (0-10), shared by the Calls
// and Leads pages so the score popup and the "Manual Score Metrics" legend
// always agree on the same thresholds. The label/blurb here are computed
// client-side from the numeric score already stored on the record, rather
// than trusting the LLM to self-report a band name, so they can never drift
// out of sync with the score itself.
export const MANUAL_SCORE_BANDS = [
  {
    key: "below_average",
    label: "Below Average",
    range: "1 to 3",
    min: 0,
    max: 3,
    blurb:
      "Calls scored 1 to 3 are Below Average because the manual entry is vague or missing key details — no clear topic, person, deadline, or next step — so the next person can't act on it without asking follow-up questions.",
  },
  {
    key: "average",
    label: "Average",
    range: "3 to 6",
    min: 3,
    max: 7,
    blurb:
      "Calls scored 3 to 6 are Average because the entry covers the basics but stays generic or incomplete — it may name a task without the reasoning, timeline, or specific outcome expected.",
  },
  {
    key: "good",
    label: "Good",
    range: "7 to 8",
    min: 7,
    max: 8,
    blurb:
      "Calls scored 7 to 8 are Good because the entry is mostly specific and actionable, naming the relevant person, document, or issue, with only minor gaps in context.",
  },
  {
    key: "excellent",
    label: "Excellent",
    range: "8 to 10",
    min: 8,
    max: 10,
    blurb:
      "Calls scored 8 to 10 are Excellent because the entry is fully specific, complete, and actionable — it clearly states who, what, why, and when, so the next person can act immediately.",
  },
];

export function getManualScoreBand(score) {
  if (score == null) return null;
  const value = Number(score);
  if (Number.isNaN(value)) return null;
  if (value < 3) return MANUAL_SCORE_BANDS[0];
  if (value < 7) return MANUAL_SCORE_BANDS[1];
  if (value < 8) return MANUAL_SCORE_BANDS[2];
  return MANUAL_SCORE_BANDS[3];
}
