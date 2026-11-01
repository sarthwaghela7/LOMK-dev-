// The three extra lines under each Open Call Aging card while ONE agent is selected on the Dashboard:
//   Same client context       open calls (in that age bucket) whose Case Status Change is "Same client context"
//   <Agent> dependency        open calls the AI found are waiting on that agent
//   Other agents dependency   open calls the AI found are waiting on other agents
// The server (services/open_call_aging.py) computes the numbers; this only decides what to show.

const count = (bucket, days) => {
  const value = Number(bucket?.[String(days)]);
  return Number.isFinite(value) ? value : 0;
};

// null = show nothing (no agent selected, or the server sent no context).
export function agingContextRows(context, selectedAgent, days) {
  if (!context || !selectedAgent || selectedAgent === "all") return null;
  const agent = context.agent || selectedAgent;
  const rows = [{ key: "sameClientContext", label: "Same client context", value: count(context.sameClientContext, days) }];
  // The two AI-read figures exist only while the server's AI read is switched ON (it is OFF by default); otherwise the cards
  // show just "Same client context" and nothing about AI at all.
  if (context.aiEnabled === true) {
    rows.push({ key: "agentDependency", label: `${agent} dependency`, value: count(context.agentDependency, days) },
      { key: "otherAgentDependency", label: "Other agents dependency", value: count(context.otherAgentDependency, days) });
  }
  return rows;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// "2026-10-14" -> "14 Oct 2026" (no time zone maths: it is a calendar date).
export function formatAiSince(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  return match ? `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}` : "";
}

// One short line under the cards about the two AI figures; "" when there is nothing to say (and always "" while the AI read is off).
export function agingContextNote(context, selectedAgent) {
  if (!context || !selectedAgent || selectedAgent === "all") return "";
  if (context.aiEnabled !== true) return "";          // AI read is off: nothing to explain
  const since = formatAiSince(context.aiSince);
  const from = since ? ` from ${since} onward` : "";
  const waiting = Number(context.unanalysed) || 0;
  const parts = [];
  if (since) parts.push(`AI dependency figures cover calls${from}.`);
  if (waiting > 0) parts.push(`${waiting} open call${waiting === 1 ? " is" : "s are"} still waiting for the AI to read ${waiting === 1 ? "its" : "their"} transcript; the two dependency figures will rise as that finishes.`);
  return parts.join(" ");
}

// What the four Open Call Aging cards show. The aging figures are fetched separately from the rest of the Dashboard, so:
//   loading   no answer yet for the CURRENT filters -> the cards show a placeholder while everything else is already visible
//   ready     the answer for the current filters (kept on screen during a quiet refresh: no flicker)
//   fallback  the aging request failed -> the summary's own figure, so the cards are never empty
// `aging` is { key, data } (data null = failed); `currentKey` identifies the filters on screen.
export function resolveAgingCards(aging, currentKey, summaryAges) {
  if (!aging || aging.key !== currentKey) return { state: "loading", ages: null, context: null };
  if (!aging.data) return { state: "fallback", ages: summaryAges || null, context: null };
  return { state: "ready", ages: aging.data.openCallAges || null, context: aging.data.openCallAgeContext || null };
}

export const AGING_PLACEHOLDER = "…";
export const agingCardValue = (cards, days) => (cards.ages ? cards.ages[days] ?? 0 : AGING_PLACEHOLDER);
