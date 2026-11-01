# Case Closing Priority Logic — Detailed Reference

## Purpose

The **View by Priority** option on the Case Closing page turns the normal, newest-first case list into an operational work queue. Its purpose is to show the calls that need the fastest response before lower-priority or unscored calls.

This document is the implementation reference for the current frontend behavior in `src/pages/CaseClosing.jsx`. It explains the scoring inputs, the exact tier order, tie-breaking, missing-data behavior, data flow, and backend work still required.

## Scope

- Applies only to the **Case Closing** page.
- Does not change Call Records or Follow Ups.
- Does not write a priority tier back to the database.
- Calculates the tier in the frontend every time the displayed list is derived.

## The Three Inputs

Each displayed call retains the original API analysis row under `call.raw`. Priority reads these fields from that object.

| Input | Field | Expected range/value | Meaning |
| --- | --- | --- | --- |
| Lead score | `call.raw.lead_score` | 0–100 | Paid-consultation readiness score |
| BOS score | `call.raw.bos_score` | 0–10 | Business Opportunity Score |
| Urgency | `call.raw.urgency_override` or `call.raw.fd_a_urgency_flag` | Flag or `NONE` | Same-day consultation override |

### Lead score rubric

| Lead score | Lead outcome |
| --- | --- |
| 70–100 | `BOOK_NOW` |
| 40–69 | `GATHER_MORE_INFO` |
| 0–39 | `NURTURE` |

### BOS rubric

| BOS score | BOS outcome | Target response time |
| --- | --- | --- |
| 8–10 | `HIGH` | Within 2 hours |
| 7–7.9 | `HIGH_MID` | Same day / within 4 hours |
| 4–6.9 | `STANDARD` | Within 24 hours |
| 0 | `N_A` | Internal routing only; not client-billable |

### Urgency overrides

An active urgency value always wins over both scores. The normal business flags are:

- NOID
- RFE
- Court Date
- Client Distressed
- Filing Deadline
- Grace Period
- Visa Expiry

The UI accepts either the normalized backend field (`urgency_override`) or the Stage 1 manual field (`fd_a_urgency_flag`). This matters because the backend field may still be its default value of `NONE` even when a user has selected an urgency flag in Stage 1.

The frontend treats a non-empty urgency value as active unless it is `NONE` or `N/A`, case-insensitively. Therefore an active urgency override can never fall through to score-based classification.

## Exact Priority Tiers

The queue has eight tiers, numbered from `0` (highest) to `7` (lowest).

| Order | Tier | Exact condition |
| ---: | --- | --- |
| 0 | `URGENT_OVERRIDE` | An active urgency value is present. Scores do not matter. |
| 1 | `BOOK_NOW` | No urgency override and `lead_score >= 70`. |
| 2 | `HIGH` | No higher condition and `bos_score >= 8`. |
| 3 | `HIGH_MID` | No higher condition and `bos_score >= 7`. |
| 4 | `GATHER_MORE_INFO` | No higher condition and `lead_score >= 40`. |
| 5 | `STANDARD` | No higher condition and `bos_score >= 4`. |
| 6 | `NURTURE` | No higher condition and `lead_score >= 0`. |
| 7 | `N_A` | No tier above applies. |

The comparisons are inclusive. For example, 70 is `BOOK_NOW`, 40 is `GATHER_MORE_INFO`, 8 is `HIGH`, 7 is `HIGH_MID`, and 4 is `STANDARD`.

## Why the Tiers Are Interleaved

Lead scoring and BOS scoring are two different business signals. They cannot be evaluated as two independent blocks when presenting one queue, because a call may have both scores.

The required precedence is:

```text
Urgent
  → Book Now
  → High BOS
  → High-Mid BOS
  → Gather More Info
  → Standard BOS
  → Nurture
  → N/A
```

This makes the business choice explicit. Examples:

| Lead | BOS | Result | Why |
| ---: | ---: | --- | --- |
| 95 | 9.2 | `BOOK_NOW` | Lead score 70+ outranks high BOS. |
| 55 | 8.5 | `HIGH` | `HIGH` is ahead of `GATHER_MORE_INFO`. |
| 55 | 7.4 | `HIGH_MID` | `HIGH_MID` is ahead of `GATHER_MORE_INFO`. |
| 35 | 5.0 | `STANDARD` | `STANDARD` is ahead of `NURTURE`. |
| 35 | 0 | `NURTURE` | BOS 0 does not create a BOS priority tier. |
| 10 | 9.0 | `HIGH` | A high BOS score outranks nurture. |
| 10 | 9.0 + RFE | `URGENT_OVERRIDE` | Urgency overrides all scores. |

## Classification Pseudocode

```text
if urgency_override is active OR fd_a_urgency_flag is active:
    return URGENT_OVERRIDE

lead = a finite numeric lead_score, otherwise no lead score
bos  = a finite numeric bos_score, otherwise no BOS score

if lead >= 70: return BOOK_NOW
if bos >= 8:   return HIGH
if bos >= 7:   return HIGH_MID
if lead >= 40: return GATHER_MORE_INFO
if bos >= 4:   return STANDARD
if lead >= 0:  return NURTURE
return N_A
```

Scores may arrive as numeric strings from an API. The frontend converts finite numeric strings such as `"70"` and `"8.5"` into numbers. `null`, `undefined`, blank strings, and non-numeric values are treated as missing; they do not accidentally qualify as zero or as a lead score below 40.

## Sorting Rules

### When View by Priority is off

Calls are explicitly sorted by `call.date` descending: newest date first.

### When View by Priority is on

1. Each call is assigned a tier using the logic above.
2. Calls sort by tier number ascending (`0` before `7`).
3. Calls in the same tier sort by the score responsible for that tier, descending.
4. If those scores match, calls sort by date descending.

| Tier | Secondary sort score |
| --- | --- |
| `URGENT_OVERRIDE` | Lead score, then BOS score when lead is missing |
| `BOOK_NOW` | Lead score |
| `HIGH` | BOS score |
| `HIGH_MID` | BOS score |
| `GATHER_MORE_INFO` | Lead score |
| `STANDARD` | BOS score |
| `NURTURE` | Lead score |
| `N_A` | No score available, so date descending |

Using the tier-relevant score is important. For example, two `HIGH` calls must be ranked by BOS score, not by an unrelated lead score they may also contain.

## No-Score and BOS-Zero Behavior

`N_A` is the final tier. It includes a call when no usable priority condition applies, commonly because both score fields are missing.

When priority view is on, the table inserts a visible **“N/A — No Score”** group row immediately before the first `N_A` call. This gives users a visible result even when the backend has not populated any score fields: unscored records remain at the bottom instead of being indistinguishable from the main queue.

A BOS value of `0` does not match `HIGH`, `HIGH_MID`, or `STANDARD`, and therefore also reaches `N_A`. Per the BOS rubric, this represents internal routing only rather than a client-billable opportunity.

## State and Render Data Flow

```text
Case Closing checkbox
  → viewByPriority state
  → displayedCalls useMemo
  → sortByPriority(filtered) when enabled
     or sortByDate(filtered) when disabled
  → displayedCalls.map(...) table rows
```

- The checkbox is controlled by `viewByPriority`.
- Its `onChange` uses the checkbox’s actual checked value.
- `viewByPriority` is in the `useMemo` dependency array, so changing the checkbox recalculates the displayed list.
- Rows use `call.id` as their React key, allowing React to move the existing records into their new order correctly.
- In priority mode, each row has a `data-priority-tier` attribute for inspection and testing.

## Data Availability and Backend Responsibilities

The frontend receives call data through `getCallsList()` in `src/data/api.js`. It preserves the full analysis API row as `raw`, which is why the priority code can read the score and urgency fields without a separate API transformation.

The database schema has these nullable fields on `CallAnalysisResult`:

```text
lead_score
bos_score
priority_tier
urgency_override
case_closing_completed
```

At present, the frontend calculates priority. The `priority_tier` database column is not relied on by the Case Closing page and should not be treated as current source-of-truth display data.

For priority order to be meaningful across the real dataset, the backend/F3 LLM processing must populate at least:

1. `lead_score` with a valid 0–100 value when applicable.
2. `bos_score` with a valid 0–10 value when applicable.
3. `urgency_override`, or continue to preserve the Stage 1 `fd_a_urgency_flag` field in the analysis response.

If all API rows have null scores and no urgency flags, all records are correctly placed in `N_A`; the priority order of the records themselves will then be their date order. The N/A group marker is intentionally shown so that this state is clear rather than silently appearing as a broken toggle.

## Validation Checklist

Use these cases when validating an API response or test data:

| Test | Data | Expected tier |
| --- | --- | --- |
| Urgency override | lead 5, BOS 0, `Urgent - RFE` | `URGENT_OVERRIDE` |
| Book Now boundary | lead 70 | `BOOK_NOW` |
| Gather boundary | lead 40, BOS absent | `GATHER_MORE_INFO` |
| Nurture boundary | lead 39, BOS absent | `NURTURE` |
| High BOS boundary | BOS 8 | `HIGH` |
| High-Mid BOS boundary | BOS 7 | `HIGH_MID` |
| Standard BOS boundary | BOS 4 | `STANDARD` |
| Mixed score precedence | lead 50, BOS 8 | `HIGH` |
| Mixed score precedence | lead 20, BOS 4 | `STANDARD` |
| Missing scores | lead null, BOS null | `N_A` and the N/A group row |
| BOS zero | lead absent, BOS 0 | `N_A` |

Also verify that enabling the toggle puts tiers in the order listed above and disabling it returns the same filtered records to newest-first date order.

## Related Files

- `src/pages/CaseClosing.jsx` — classification, sorting, toggle, and N/A group rendering.
- `src/data/api.js` — maps API analysis rows into `call` objects and retains `raw` data.
- `src/App.css` — N/A group-row styling.
- `CASE_CLOSING_IMPLEMENTATION_SUMMARY.md` — broader Case Closing implementation notes.
