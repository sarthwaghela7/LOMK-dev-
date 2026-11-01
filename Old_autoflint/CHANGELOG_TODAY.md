# Today's Changes — July 25, 2026

## Change 1: VMU Sent KPI Card (Dashboard)

### What was the problem?
The dashboard had a "Voicemail Calls" card showing the total number of voicemail calls, but there was no way to see which voicemail calls were actually answered by a front desk agent versus which ones were just left as messages. Users had to dig through the call records to figure this out.

### What did we build?
We added a new **"VMU Sent"** KPI card in the **Call Types** belt of the dashboard. This card has two clickable numbers:

- **Yes** → Shows how many voicemail calls were answered by a known front desk agent (Shrishti, Manaswi, Satish, or Gokul)
- **No** → Shows how many voicemail calls were left as messages with no agent answering

### How does it work?
- Clicking "Yes" or "No" opens a popup listing all the calls in that category
- Each call row has a **dropdown** where you can change the `voicemail_left_if_no_answer` field between Yes/No
- Changing the dropdown saves to the database instantly using the existing API endpoint
- The Yes/No counts on the card update immediately when you make a change
- The original "Voicemail Calls" card (total count) is still there unchanged

### Files changed:
- `src/pages/Dashboard.jsx` — Added VMU Sent card to kpiCards array, added modal config, updated Call Types belt layout to show both VMU Sent and Voicemail Calls cards
- `src/utils/reportMetrics.js` — Added `vmuSentModalItems`, `vmuSentYes`, `vmuSentNo` to `buildMetrics()` output

**No backend changes.**

---

## Change 2: Bug Fix — KPI Totals Were All Over The Place

### What was the problem?
Five dashboard cards were showing different totals even though they all should have been counting the same thing (calls with completed AI analysis):

| Card | Was Showing | Should Be |
|---|---|---|
| Client Type Distribution | 86 | 73 |
| Client Sentiment | 86 | 73 |
| Quality Score Distribution | Header said "86 analyzed" but distribution sum was 83 | Both should match at 73 |
| Top Case Types | 64 | 73 |
| Top Lead Sources | 86 | 73 |

This was confusing — users couldn't trust the numbers when they didn't add up.

### What was causing it?
The `buildMetrics()` function inside `reportMetrics.js` creates a variable called `analysis` which holds ALL analysis rows after deduplication (86 rows). But not all of these rows have completed processing — some are still pending, failed, or not yet analyzed.

Five dashboard cards were looping over this full `analysis` array (86 rows) instead of only counting rows where the AI analysis was actually complete (73 rows). The Inbound/Outbound cards were correct because they use a different data source (`records`).

### What did we fix?
1. Added a new variable: `analysisCompleted = analysis.filter(isAnalysisRowComplete)` — this filters to only rows marked as "processed", "complete", "completed", "success", or has a summary (using the existing `isAnalysisRowComplete` function from `agentAttribution.js`)
2. Changed the 5 buggy cards to iterate `analysisCompleted` instead of `analysis`:
   - Client Type: `analysisCompleted.forEach(...)` instead of `analysis.forEach(...)`
   - Score Distribution: `analysisCompleted.map(...)` instead of `analysis.map(...)`
   - Sentiment: `analysisCompleted.forEach(...)` instead of `analysis.forEach(...)`
   - Case Types: `analysisCompleted.forEach(...)` instead of `analysis.forEach(...)`
   - Lead Sources: `analysisCompleted.forEach(...)` instead of `analysis.forEach(...)`
3. Changed `totalAnalysis` from `analysis.length` to `analysisCompleted.length` — this fixes the Quality Score header that says "X analyzed"
4. Fixed rate denominators — `positiveRate`, `negativeRate`, `voicemail`, and `escalation` rates were dividing by `analysis.length` (86) instead of `analysisCompleted.length` (73)

### Files changed:
- `src/utils/reportMetrics.js` — Added `analysisCompleted` variable, updated all 5 card loops and rate formulas

**No backend changes.**

---

## Change 3: Bug Fix — "Other" vs "N/A" Agent Labels Mismatched

### What was the problem?
The dashboard/report had two tables showing agent-related data, but they used different names and counts for the same thing:

| Table | Label Used | Count |
|---|---|---|
| Call Volume by Agent | "N/A" | 27 |
| Call Volume by Agent | "Other" | 0 |
| Agent Performance | "Other" | 26 |

The "N/A" / "Other" concept refers to calls that couldn't be matched to a known agent (Shrishti, Manaswi, Satish, Gokul). There were two issues:

1. **Different labels**: One table called them "N/A", the other called them "Other"
2. **Different counts**: The volume table counted from call records (27 calls) while the performance table counted from analysis rows (26 analysis entries). These will never match because not every call record has an analysis row.

### What did we fix?
**Step 1 — Separate N/A from Other properly:**

We updated `volumeBucketForAgent()` so that:
- Empty/null front desk name → "N/A"
- Has a name but doesn't match known agents → "Other"
- Matches a known agent → their name

Before this fix, both cases were lumped together as one bucket.

**Step 2 — Make both tables count from the same source:**

We changed the **Call Volume by Agent** loop to iterate over `analysis` rows (same as the Agent Performance table) instead of `records`. Each analysis row is enriched with data from its linked call record. This way both tables always agree on the numbers.

**Step 3 — Pad the agents array:**

Added automatic padding so "N/A" and "Other" always appear as rows in the agent tables even if they have 0 calls (just like the 4 known agents always appear).

**Step 4 — Hide empty N/A row:**

If "N/A" has 0 calls, it's hidden from the tables since it's meaningless to show an empty row.

**Step 5 — Update display names:**

Changed `agentDisplayName()` to map "Unassigned" → "N/A" instead of "Other". Updated all filters to exclude both "N/A" and "Other" from per-agent comparison charts and subsections.

### What changed visually?

**Before:**
| Agent (Volume) | Calls | | Agent (Performance) | Calls |
|---|---|---|---|---|
| Shrishti | 20 | | Shrishti | 20 |
| Manaswi | 18 | | Manaswi | 18 |
| Satish | 12 | | Satish | 12 |
| Gokul | 9 | | Gokul | 9 |
| Other | 0 | | Other | 26 |
| N/A | 27 | | | |

**After:**
| Agent (Volume) | Calls | | Agent (Performance) | Calls |
|---|---|---|---|---|
| Shrishti | 20 | | Shrishti | 20 |
| Manaswi | 18 | | Manaswi | 18 |
| Satish | 12 | | Satish | 12 |
| Gokul | 9 | | Gokul | 9 |
| Other | 2 | | Other | 2 |
| N/A | 24 | | N/A | 24 |

Both tables now show the same counts for the same labels.

### Files changed:
- `src/utils/reportMetrics.js` — Changed `volumeBucketForAgent()`, changed volume loop from `records` to `analysis`, added N/A/Other padding in agents array, split agent naming in agents loop
- `src/pages/ReportAnalysis.jsx` — Updated `agentDisplayName()`, updated all `a.name !== "Unassigned"` filters to exclude N/A and Other, hide empty N/A rows in all three table renders (PDF + on-screen)

**No backend changes.**

---

## Summary of Files Changed

| File | What We Changed |
|---|---|
| `src/utils/reportMetrics.js` | Added `analysisCompleted` filter, fixed 5 KPI card loops to use completed-only rows, split N/A vs Other in agent volume bucket function, changed volume loop to iterate analysis rows, added N/A/Other padding to agents array, split agents loop to distinguish Other from Unassigned, added vmuSentModalItems for VMU Sent card, fixed rate denominators |
| `src/pages/Dashboard.jsx` | Added VMU Sent KPI card with Yes/No sub-items, added vmu_sent modal config (editable), updated Call Types belt to include both VMU Sent and Voicemail Calls cards, fixed belt index references for the new card position |
| `src/pages/ReportAnalysis.jsx` | Changed `agentDisplayName("Unassigned")` to return "N/A", updated all agent filter conditions to exclude N/A/Other from charts/subsections, hide N/A rows with 0 calls in all three table renders |

## What We Did NOT Change
- **No backend code** was touched — no new API endpoints, no database migrations, no schema changes
- **No styling** was changed — all existing layout, colors, spacing, and CSS remain as-is
- **No other pages** were affected — CallRecords, CallAnalysis, Settings, etc. are untouched
