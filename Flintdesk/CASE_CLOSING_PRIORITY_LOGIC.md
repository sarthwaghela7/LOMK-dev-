# Case Closing Priority Sorting Logic

## Overview

The **Case Closing** page sorts calls by priority using a comprehensive system based on two reference frameworks:

1. **Paid Consultation Lead Scoring Rubric**
2. **Business Opportunity Score (BOS) Methodology**

This document explains how priority sorting is implemented and how calls appear in the priority-sorted view.

---

## Priority Tiers

Calls are assigned to one of seven priority tiers (from highest to lowest priority):

### Tier 1: URGENT_OVERRIDE ⭐⭐⭐
**Condition**: Urgency override flag is set (regardless of score)

**Urgency Override Flags**:
- `NOID` — Notice of Intent to Deny
- `RFE` — Request for Evidence
- `COURT_DATE` — Upcoming court date
- `CLIENT_DISTRESSED` — Client reports extreme stress/urgency

**Action**: Same-day consultation offered regardless of lead score or BOS score.

**Example**: A call with `lead_score=35` (normally NURTURE tier) but with `urgency_override=RFE` jumps to URGENT_OVERRIDE and appears first.

---

### Tier 2: BOOK_NOW 🔥
**Condition**: `lead_score >= 70` (and no urgency override)

**Lead Scoring Rationale**:
- Score 70-100 indicates highest readiness to book a consultation
- Clients in this range have demonstrated:
  - Clear case definition
  - Financial readiness
  - Urgency alignment with services
  - High likelihood of conversion

**Sort Within Tier**: By lead_score (descending, highest first)

**Example**: 
- Call A: lead_score=95 → appears before
- Call B: lead_score=72 → appears after

---

### Tier 3: HIGH (Business Opportunity)
**Condition**: `bos_score >= 8.0` (and no urgency override or lead_score qualification)

**BOS Rationale**:
- Score 8-10 indicates high business potential
- Clients represent significant opportunity for:
  - Paid consultation revenue
  - Case complexity/scope
  - Long-term relationship value

**SLA**: Should be responded to within 2 hours

**Sort Within Tier**: By bos_score (descending)

**Example**:
- Call A: bos_score=9.5 → appears before
- Call B: bos_score=8.2 → appears after

---

### Tier 4: HIGH_MID (Business Opportunity)
**Condition**: `bos_score >= 7.0 AND bos_score < 8.0` (and no higher tier qualification)

**BOS Rationale**:
- Score 7-7.9 indicates solid business opportunity
- Slightly lower conversion probability than HIGH tier

**SLA**: Should be responded to same day / within 4 hours

**Sort Within Tier**: By bos_score (descending)

---

### Tier 5: GATHER_MORE_INFO
**Condition**: `lead_score >= 40 AND lead_score < 70` (and no urgency override or BOS qualification)

**Lead Scoring Rationale**:
- Score 40-69 indicates medium readiness
- Clients need additional information before booking consultation
- Likely to convert with proper nurturing/follow-up

**Action**: Follow up with educational content, service details, pricing info, etc.

**Sort Within Tier**: By lead_score (descending)

**Example**:
- Call A: lead_score=65 → appears before
- Call B: lead_score=42 → appears after

---

### Tier 6: STANDARD (Business Opportunity)
**Condition**: `bos_score >= 4.0 AND bos_score < 7.0` (and no higher tier qualification)

**BOS Rationale**:
- Score 4-6.9 indicates standard/baseline business potential
- Standard service-line inquiry, lower complexity

**SLA**: Should be responded to within 24 hours

**Sort Within Tier**: By bos_score (descending)

---

### Tier 7: NURTURE
**Condition**: `lead_score < 40` (and no urgency override, BOS, or other qualification)

**Lead Scoring Rationale**:
- Score 0-39 indicates low immediate readiness
- Clients are exploratory/information-seeking
- Require nurturing/relationship building over time

**Action**: Add to nurture sequence, educational emails, etc.

**Sort Within Tier**: By lead_score (descending)

---

### Tier 8: N_A
**Condition**: No lead_score, no bos_score, no urgency_override

**Status**: Unable to classify; insufficient data.

**Sort Within Tier**: By call date (most recent first)

---

## Sorting Algorithm

```
1. Group all calls by priority tier (in order: URGENT_OVERRIDE → BOOK_NOW → HIGH → HIGH_MID → GATHER_MORE_INFO → STANDARD → NURTURE → N_A)

2. Within each tier, sort by:
   - Tier 1 (URGENT_OVERRIDE): by lead_score DESC (if available), then bos_score DESC, then date DESC
   - Tier 2 (BOOK_NOW): by lead_score DESC
   - Tier 3 (HIGH): by bos_score DESC
   - Tier 4 (HIGH_MID): by bos_score DESC
   - Tier 5 (GATHER_MORE_INFO): by lead_score DESC
   - Tier 6 (STANDARD): by bos_score DESC
   - Tier 7 (NURTURE): by lead_score DESC
   - Tier 8 (N_A): by call_date DESC (most recent first)

3. Return sorted list to frontend
```

---

## Data Sources

### Lead Score
- **Origin**: Calculated by FlintDesk LLM during initial call analysis (fd_llm_node.py)
- **Storage**: `CallAnalysisResult.lead_score` (Integer, 0-100)
- **Availability**: Set during F3 processing (LLM analysis stage)

### BOS Score
- **Origin**: Calculated by FlintDesk LLM during initial call analysis (fd_llm_node.py)
- **Storage**: `CallAnalysisResult.bos_score` (Float, 0-10)
- **Availability**: Set during F3 processing (LLM analysis stage)

### Urgency Override
- **Origin**: Can be manually set from Calls page Stage 1 field (`fd_a_urgency_flag`)
- **Storage**: `CallAnalysisResult.urgency_override` (Text, one of: NOID, RFE, COURT_DATE, CLIENT_DISTRESSED, NONE)
- **Mapping**:
  - "Urgent - NOID" → `NOID`
  - "Urgent - RFE" → `RFE`
  - "Court Date" → `COURT_DATE`
  - "Client Distressed" → `CLIENT_DISTRESSED`
  - Other values → `NONE`

### Priority Tier
- **Origin**: Computed from lead_score, bos_score, and urgency_override using the sorting algorithm above
- **Storage**: `CallAnalysisResult.priority_tier` (Text)
- **Calculation**: Performed by backend when call is created/updated OR by frontend when displaying
- **Recalculation**: Whenever any of (lead_score, bos_score, urgency_override) change

---

## Frontend Implementation

### Default View (Off)
When **View by Priority** is OFF:
- Calls are sorted by call_date DESC (most recent first)
- Priority tier is not visible

### Priority View (On)
When **View by Priority** is ON:
1. Fetch all calls for the selected date range
2. Compute/retrieve priority_tier for each call (backend provides this)
3. Group calls by tier using the tier ordering defined above
4. Within each tier, apply the secondary sort
5. Display calls in priority order

### Optional: Visual Indicators
The frontend can display priority tier visually:
- **Color coding**: Different background color per tier
- **Badge/label**: Show tier name (URGENT_OVERRIDE, BOOK_NOW, etc.)
- **Score display**: Show lead_score or bos_score in parentheses

**Example Row**:
```
| AF#1234 | John Doe | (URGENT - RFE)     🔴 | ... | Lead Score: 45 | BOS: 8.5 |
| AF#1235 | Jane Doe | BOOK_NOW          🔥 | ... | Lead Score: 82 | BOS: 6.2 |
| AF#1236 | Bob Test | GATHER_MORE_INFO  🟡 | ... | Lead Score: 55 | BOS: 5.1 |
```

---

## Reference Documents

This implementation follows the structure and intent of:

1. **PAID CONSULTATION — LEAD SCORING RUBRIC**
   - Defines lead score ranges and their meanings
   - Establishes the BOOK_NOW / GATHER_MORE_INFO / NURTURE tiers
   - Specifies urgency override conditions

2. **BUSINESS OPPORTUNITY SCORE — METHODOLOGY AND PRIORITY GUIDE**
   - Defines BOS ranges (8-10, 7-7.9, 4-6.9, 0/N/A)
   - Establishes SLA response times
   - Provides context for case complexity and revenue impact

3. **MK Immigration Case Lifecycle Map**
   - Provides context for case types and service lines
   - Supports future enhancements for service-line-specific prioritization

---

## Future Enhancements

- **Service-line weighting**: Adjust priority tiers based on service line (e.g., Employment-based vs. Family-based)
- **Workload balancing**: Consider agent workload when assigning calls to team members
- **Dynamic re-prioritization**: Automatically adjust priority if urgency flag is changed or new information becomes available
- **SLA tracking**: Display time-in-tier and alert if SLA is approaching expiration

---

## Testing & Validation

To verify priority sorting is working correctly:

1. **Single urgency override**: Create a call with low lead_score but NOID urgency → should appear in URGENT_OVERRIDE tier
2. **Tier boundary**: Create calls at tier boundaries (lead_score=69, 70, 39, 40, etc.) → verify they sort correctly
3. **BOS vs Lead**: Create calls with high BOS but low lead_score → verify BOS tier > NURTURE tier
4. **No data**: Create a call with no lead_score and no bos_score → should appear in N_A tier at the end

---

## Implementation Files

- **Backend Migration**: `migrations/017_add_case_closing_fields.py`
- **Backend Models**: Updated `storage/models.py` (CallAnalysisResult)
- **Frontend Page**: `src/pages/CaseClosing.jsx` (based on Calls.jsx)
- **Frontend Utilities**: Priority calculation helper (inline in CaseClosing.jsx or separate utility)
- **API Endpoints**: Extended to expose priority_tier, lead_score, bos_score fields
- **Documentation**: This file (`CASE_CLOSING_PRIORITY_LOGIC.md`)
