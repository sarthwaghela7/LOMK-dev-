import { CollapsiblePanel } from "./CollapsiblePanel.jsx";

const guideSections = [
  {
    icon: "fa-arrow-up-right-dots",
    title: "For Outbound Calls",
    steps: [
      <>Select the date range you want to review.</>,
      <>Use <strong>All Agents</strong> and select your name.</>,
      <>Review the outbound calls associated with you for that date range.</>,
    ],
  },
  {
    icon: "fa-arrow-down-wide-short",
    title: "For Inbound Calls",
    steps: [
      <>Select the date range, then open <strong>Call Direction</strong>.</>,
      <>Choose <strong>Inbound</strong> (the other option is Outbound).</>,
      <>Match the client phone number, date, and timestamp to find your call.</>,
      <>Once you identify your call, click the <strong>Agent</strong> column and write your name in it.</>,
    ],
  },
  {
    icon: "fa-circle-check",
    title: "How to Cross-Check a Call",
    steps: [
      <>Match the client phone number, date, and timestamp.</>,
      <>Open the record and review its <strong>transcript</strong>.</>,
      <>Listen to the <strong>audio</strong> to verify the conversation details.</>,
    ],
  },
];

export function CallRecordsUsageGuide() {
  return (
    <CollapsiblePanel
      id="calls-usage-guide"
      className="call-records-usage-guide"
      title="How to Find and Verify Calls"
    >
      <p className="collapsible-panel-lead">Use the filters above to find outbound or inbound calls and cross-check the call details.</p>
      <div className="call-records-usage-guide-sections">
        {guideSections.map((section) => (
          <section key={section.title} className="call-records-usage-guide-section">
            <h3><i className={`fa-solid ${section.icon}`} aria-hidden="true" />{section.title}</h3>
            <ol>
              {section.steps.map((step, index) => <li key={index}>{step}</li>)}
            </ol>
          </section>
        ))}
      </div>
    </CollapsiblePanel>
  );
}
