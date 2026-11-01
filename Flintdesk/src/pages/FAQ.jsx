// Help page for every signed-in user. Each answer below was written from the current Calls page code
// (pages/Calls.jsx, data/submitState.js, data/unsavedChanges.js, components/FollowUpDatesPopover.jsx and the
// matching backend rules). If that behaviour changes, update the matching answer here.
const SECTIONS = [
  {
    id: "follow-up-dates",
    title: "Follow Up Dates",
    items: [
      {
        q: "What is the “Follow Up Dates” button at the top of the Calls page for?",
        a: [
          "It is a quick filter. It lists every follow-up date that appears on the calls matching your current filters (date range, agent and so on), so you can see which calls are due for follow-up on a given day.",
          "Click a date and the table shows only the calls that have that date in their Follow Up Dates column. A chip such as “Follow-up: 10/14/2026” appears next to the button; click × on the chip, or “Clear selection” in the list, to go back to all calls.",
        ],
      },
      {
        q: "Can I add or change follow-up dates in that popup?",
        a: [
          "No. The popup only lists dates and filters the table. It never changes any call.",
          "To add or change the dates on a call, use the Follow Up Dates cell in the Stage 2 · Action Tracking group of that call's row (see the next question).",
        ],
      },
      {
        q: "How do I enter or edit follow-up dates on a call?",
        a: [
          "Open the Follow Up Dates cell of the call. Type a date (for example 10-14-2026) or a short note in the box and press Enter or click Add. Use × next to an entry to remove it, then save the cell.",
          "Entries are kept in the order you add them and a call can have several. A typed date that has not been added with the Add button is still included when you save.",
          "Follow Up Dates is one of the four required Stage 2 fields. If no follow-up is needed, the automation (or you) enters “N/A”; leaving it empty blocks Submit.",
        ],
      },
      {
        q: "Does the date have to be in a particular format?",
        a: [
          "The field accepts free text, so nothing is rejected on entry. Only entries written as MM-DD-YYYY (or MM/DD/YYYY, YYYY-MM-DD) are recognised as dates. Anything else, such as “N/A” or a note, is kept on the call but does not appear in the Follow Up Dates popup or match its filter.",
          "Dates are calendar dates with no time zone, so they never shift by a day.",
        ],
      },
      {
        q: "Why does the popup say “No follow-up dates in this date range”?",
        a: [
          "None of the calls matching your current filters has a recognised follow-up date. Widen the date range or clear a filter and open the popup again; the list updates with the filter belt.",
        ],
      },
    ],
  },
  {
    id: "save-submit",
    title: "Saving, Submitting and Completing a Call",
    items: [
      {
        q: "Is there a Save button?",
        a: [
          "No. On a call that is not completed yet, each field is saved automatically as soon as you finish editing it. The row briefly shows “Saving…” and then “Saved ✓” once the server has confirmed it.",
          "If a save fails, the row shows “Save failed” with a Retry button. Your value is not treated as saved until the server confirms it.",
        ],
      },
      {
        q: "What does “Unsaved Changes” mean?",
        a: [
          "It appears on a row when an edit is being held and has not been sent to the server yet. This happens when you edit a call that is already Completed: those edits wait until you click Submit. Deleting a value that was already saved on a Completed call (the row also shows “IMP – Data deleted”) counts too.",
          "While it is showing, the row's button changes to “Submit Changes” and turns orange. Nothing has been saved yet, so click Submit Changes to save.",
          "If you edit a value and then type the original value back, the change cancels itself and the badge disappears.",
        ],
      },
      {
        q: "What do the Submit button's labels mean?",
        a: [
          "Submit (clickable): the call's Completed state has changed (for example you set Case Status Change to Success PC) but has not been recorded yet. Click Submit to record it.",
          "Submit Changes (orange, with the Unsaved Changes badge; clickable): you have held edits that Submit will save.",
          "Retry Submit (orange, with “Save failed”; clickable): the last Submit failed. Nothing was marked saved; click it to try again.",
          "Submitting…: the request is running. Wait for it to finish.",
          "Submitted (disabled): Submit succeeded during this visit to the page. After you reload, the button shows Saved instead, because only the page remembers a Submit.",
          "Saved (disabled): everything on the row matches what is stored and there is nothing to submit.",
          "Submit (disabled, hover for the reason): a required Stage 2 field is still empty. The hover text lists the missing fields.",
        ],
      },
      {
        q: "Why is the Submit button so dark?",
        a: [
          "The dark, almost black green is the standard button colour in FlintDesk, so a dark Submit is the normal look of a button. Orange (amber) means “you have unsaved or failed changes”; green “Completed” is the separate indicator next to it.",
          "Disabled versions cannot be clicked, either because nothing needs submitting or because a required field is missing; hover over the button to read why.",
        ],
      },
      {
        q: "What is the difference between saving and submitting?",
        a: [
          "Saving stores what you typed in a field. On an open call this happens automatically, field by field.",
          "Submit is the explicit finishing step. It first sends any held edits, then works out whether the call is Completed and records that on the call. It can only be used once the required Stage 2 fields are filled in.",
          "Submit does not send a message to anyone, and it does not change Case Status Change itself. The status you chose is what decides Completed.",
        ],
      },
      {
        q: "Which Stage 2 fields must be filled before I can Submit?",
        a: [
          "Self Action Item, Action Status, Dependency and Follow Up Dates. Action Due Date and Blocker Comment are optional and never block Submit.",
          "The Stage 3 and Stage 4 fields stay locked until these four are filled in.",
        ],
      },
      {
        q: "What does “Completed” mean, and how does a call become Completed?",
        a: [
          "The Completed indicator turns green when Case Status Change is Success PC, Success GE or Dropped. You cannot click it; it follows the status automatically.",
          "The status is stored as completed on the call when you click Submit. Until then, the row can show Completed while the button still says Submit, which means the Submit step is still outstanding.",
          "Changing the status away from those three values and submitting removes the Completed state. Completed calls also appear under the Completed option of the Submitted / Completed filter.",
        ],
      },
      {
        q: "What happens if I leave the page with unsaved changes?",
        a: [
          "If any row still has held edits, a save in progress or a failed save, FlintDesk asks “You have changes that are not saved yet. Leave without saving them?” when you use the sidebar to go to another page. Choose Cancel to stay and click Submit Changes first.",
          "Closing or refreshing the browser tab shows your browser's own warning in the same situation.",
          "When nothing is unsaved, you can leave without any prompt.",
        ],
      },
      {
        q: "Are Self Action Item, Action Due Date and Follow Up Dates filled in by AI?",
        a: [
          "Those three Stage 2 fields can be pre-filled by an AI step after a call is analysed, and the column labels mark them as AI-automated. Always check them: you can edit any of them, and the Submit rules above apply to the final values.",
        ],
      },
    ],
  },
];

export default function FAQ() {
  return (
    <section className="process-page faq-page">
      <div className="leads-heading">
        <h1>FAQ</h1>
      </div>
      <p className="muted faq-intro">Plain-language answers about the Calls page. Select a question to read the answer.</p>
      <nav className="faq-jump" aria-label="FAQ sections">
        {SECTIONS.map((section) => <a key={section.id} href={`#faq-${section.id}`}>{section.title}</a>)}
      </nav>
      {SECTIONS.map((section) => (
        <article key={section.id} id={`faq-${section.id}`} className="faq-section">
          <h2>{section.title}</h2>
          {section.items.map((item) => (
            <details key={item.q} className="faq-item">
              <summary>{item.q}</summary>
              <div className="faq-answer">{item.a.map((line) => <p key={line}>{line}</p>)}</div>
            </details>
          ))}
        </article>
      ))}
    </section>
  );
}
