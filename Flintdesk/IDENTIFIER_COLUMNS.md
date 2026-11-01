# Client ID and Lead ID columns

- **Calls:** Client ID comes from `calls.client_id`; Lead ID comes from the
  one-to-one `fd_leads.fd_a_leads_call_id` relationship. Both identifiers open
  their corresponding details dialog. Call, Client ID, and Lead ID filters are
  applied to the same displayed/exported rows.
- **Dependencies:** The stored Call ID is resolved against the canonical call
  UUID, call reference, or formatted call number. Client ID and Lead ID are
  derived from that verified call relationship and are included in export.
- **Leads:** Lead ID is the immutable `fd_m_leads_lead_id`. Client ID is shown
  only after Analyse links the lead to a call; unmatched S1 leads correctly
  have no Client ID.
- **Tasks:** intentionally out of scope. Task records have no verified Call
  relationship, and phone matching can be ambiguous. Identifier columns should
  be added only after a Task-to-Call relationship and population workflow are
  defined.
