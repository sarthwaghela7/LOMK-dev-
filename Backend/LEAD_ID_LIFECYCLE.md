# Lead ID lifecycle

## Identity and creation

`fd_leads.fd_m_leads_lead_id` is the required, unique business Lead ID. It
originates in the FlintDesk manual/CSV import workflow; Vitel call ingestion
does not create Lead IDs. Once created, the business Lead ID is immutable.

## Call association

A user explicitly triggers **Analyse** for a lead. The backend normalizes the
lead phone and checks both sides of eligible calls, so inbound and outbound
calls can match. The newest available call with AI analysis is selected. The
nullable `fd_leads.fd_a_leads_call_id` UUID foreign key stores the association.

The relationship is optional one-to-one:

- A call can exist without a Lead ID.
- A lead can exist without a Call ID.
- A lead can reference at most one call.
- A call can be referenced by at most one lead.

Missing Lead IDs are represented as a neutral em dash in AutoFlint. A linked
Lead ID opens the persisted lead details returned by the authenticated backend.

## Updates and retirement

Lead attributes can follow the existing FlintDesk update workflow, but the
Lead ID itself cannot be edited. Correcting an identity requires deleting and
recreating the lead. The existing hard-delete workflow is the retirement
mechanism; archival/history is intentionally deferred until a real archive
state is designed.

## Access

Authenticated AutoFlint users receive the currently verified lead fields.
Field-level lead permissions are not invented here; role-based restriction is
a follow-up if the product introduces that policy.
