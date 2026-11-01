import { CollapsiblePanel } from "./CollapsiblePanel.jsx";

export function LeadOwnershipNotice() {
  return (
    <CollapsiblePanel
      id="lead-ownership-notice"
      className="lead-ownership-notice"
      tone="warning"
      icon="fa-triangle-exclamation"
      title="Important: Every Lead Must Have an Owner"
    >
      <p>When importing leads into S1, make sure that every imported lead has an owner assigned.</p>
      <strong>Every row must have an Owner. No lead should remain unassigned.</strong>
      <p>Please verify the Owner field for all imported leads before completing the import.</p>
    </CollapsiblePanel>
  );
}
