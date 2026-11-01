import { useCallback, useEffect, useMemo, useState } from "react";
import { getFdPrompts, rollbackFdPrompt, saveFdPrompt } from "../data/api.js";
import { setUnsavedCount } from "../data/unsavedChanges.js";
import {
  PROMPT_FIELDS, PROMPT_MAX_CHARS, accessNotice, canSave, draftOf, earlierVersions, formatPromptTimestamp, friendlyPromptError, isDirty, unsavedPromptCount,
} from "../data/promptManagement.js";

// Settings > Prompt Management: the active AI prompts, one card each. Everyone who may open Settings can READ them; only an Admin
// (`canEdit`) gets Edit / Save / Cancel / Roll back. The API enforces the same rule (403 for anyone else), so hiding the controls
// is a convenience, not the protection. A read-only user is never given a text field at all.
export default function PromptManagementPanel({ canEdit }) {
  const [data, setData] = useState({ active: [], history: [] });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [editing, setEditing] = useState({});          // promptKey -> true while that card is in edit mode
  const [drafts, setDrafts] = useState({});            // promptKey -> { system_prompt, user_prompt_template }
  const [busy, setBusy] = useState("");                // promptKey whose request is running
  const [feedback, setFeedback] = useState({});        // promptKey -> { tone: "success" | "error", text }
  const notice = accessNotice(canEdit);

  const load = useCallback(async () => {
    try {
      const result = await getFdPrompts(true);
      setData({ active: result.active || [], history: result.history || [] });
      setLoadError("");
    } catch (error) {
      setLoadError(friendlyPromptError(error) || "Unable to load the prompts.");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  // Leaving Settings (or closing the tab) with an unsaved prompt edit asks first, through the app's existing leave guard.
  const unsaved = useMemo(() => unsavedPromptCount(drafts, data.active), [drafts, data.active]);
  useEffect(() => { setUnsavedCount("settings-prompts", unsaved); }, [unsaved]);
  useEffect(() => () => setUnsavedCount("settings-prompts", 0), []);

  const setFeedbackFor = (key, tone, text) => setFeedback((current) => ({ ...current, [key]: text ? { tone, text } : undefined }));
  const startEdit = (row) => {
    setDrafts((current) => ({ ...current, [row.prompt_key]: draftOf(row) }));
    setEditing((current) => ({ ...current, [row.prompt_key]: true }));
    setFeedbackFor(row.prompt_key, "", "");
  };
  const stopEdit = (key) => {
    setEditing((current) => ({ ...current, [key]: false }));
    setDrafts((current) => { const next = { ...current }; delete next[key]; return next; });
  };
  const cancelEdit = (row) => {
    if (isDirty(drafts[row.prompt_key], row) && !window.confirm("Discard your unsaved changes to this prompt?")) return;
    stopEdit(row.prompt_key);
  };

  const run = async (row, action, success) => {
    setBusy(row.prompt_key);
    setFeedbackFor(row.prompt_key, "", "");
    try {
      await action();
      await load();
      stopEdit(row.prompt_key);
      setFeedbackFor(row.prompt_key, "success", success);
    } catch (error) {
      setFeedbackFor(row.prompt_key, "error", friendlyPromptError(error));
    } finally {
      setBusy("");
    }
  };
  const save = (row) => run(row, () => saveFdPrompt(row.prompt_key, drafts[row.prompt_key].system_prompt, drafts[row.prompt_key].user_prompt_template), "Saved as a new version; it is now the active prompt.");
  const rollBack = (row, version) => {
    if (!window.confirm(`Make version ${version} of "${row.prompt_name}" the active prompt again?`)) return;
    run(row, () => rollbackFdPrompt(row.prompt_key, version), `Version ${version} is the active prompt again.`);
  };

  return (
    <section className="settings-card prompt-mgmt" aria-labelledby="prompt-mgmt-title">
      <header className="prompt-mgmt-header">
        <div>
          <h2 id="prompt-mgmt-title">Prompt Management</h2>
          <p>The instructions the AI follows to fill Self Action Item, Action Due Date and Follow-Up Date. Saving never overwrites an earlier version, and the 72-hour due-date limit is enforced by the server whatever the prompt says.</p>
        </div>
        <span className={`prompt-mgmt-access ${notice.tone}`}><i className={`fa-solid ${canEdit ? "fa-pen" : "fa-lock"}`} aria-hidden="true" /> {notice.label}</span>
      </header>
      <p className={`prompt-mgmt-banner ${notice.tone}`} role="note">{notice.text}</p>
      {loadError && <p className="prompt-mgmt-message error" role="alert">{loadError}</p>}
      {loading && !loadError && <p className="settings-muted" role="status">Loading prompts...</p>}
      {data.active.map((row) => {
        const key = row.prompt_key;
        const isEditing = Boolean(editing[key]) && canEdit;
        const draft = drafts[key] || draftOf(row);
        const dirty = isEditing && isDirty(draft, row);
        const earlier = earlierVersions(data.history, key);
        const message = feedback[key];
        const working = busy === key;
        const updated = formatPromptTimestamp(row.updated_at);
        return (
          <article className={`prompt-card ${isEditing ? "is-editing" : ""}`} key={key} aria-label={row.prompt_name}>
            <div className="prompt-card-head">
              <div>
                <h3>{row.prompt_name}</h3>
                <code className="prompt-key">{key}</code>
              </div>
              <div className="prompt-card-meta">
                <span className="prompt-version-badge"><i className="fa-solid fa-circle-check" aria-hidden="true" /> Active · v{row.version}</span>
                {(row.updated_by || updated) && <span className="settings-muted">Updated {updated}{row.updated_by ? ` by ${row.updated_by}` : ""}</span>}
              </div>
            </div>
            {PROMPT_FIELDS.map(({ key: field, label, rows }) => (
              <div className="prompt-field" key={field}>
                <label className="prompt-field-label" htmlFor={`${key}-${field}`}>{label}{!isEditing && <span className="prompt-readonly-tag">Read only</span>}</label>
                {isEditing ? (
                  <>
                    <textarea id={`${key}-${field}`} className="prompt-text prompt-text-edit" rows={rows} value={draft[field]} disabled={working}
                      onChange={(event) => setDrafts((current) => ({ ...current, [key]: { ...draft, [field]: event.target.value } }))} />
                    <span className={`prompt-counter ${draft[field].length > PROMPT_MAX_CHARS ? "over" : ""}`} role="status">
                      {draft[field].length.toLocaleString("en-US")} / {PROMPT_MAX_CHARS.toLocaleString("en-US")} characters{draft[field].length > PROMPT_MAX_CHARS ? " - too long to save" : ""}
                    </span>
                  </>
                ) : (
                  <pre id={`${key}-${field}`} className="prompt-text" tabIndex={0} aria-readonly="true">{row[field] || "(empty)"}</pre>
                )}
              </div>
            ))}
            {message && <p className={`prompt-mgmt-message ${message.tone}`} role={message.tone === "error" ? "alert" : "status"}>{message.text}</p>}
            {canEdit && (
              <footer className="prompt-card-actions">
                {isEditing ? (
                  <>
                    <button type="button" className="primary-button" disabled={working || !canSave(draft, row)} onClick={() => save(row)}>{working ? "Saving..." : "Save as new version"}</button>
                    <button type="button" className="table-action" disabled={working} onClick={() => cancelEdit(row)}>Cancel</button>
                    {dirty && <span className="prompt-unsaved" role="status">Unsaved changes</span>}
                  </>
                ) : (
                  <>
                    <button type="button" className="table-action" disabled={working} onClick={() => startEdit(row)}><i className="fa-solid fa-pen" aria-hidden="true" /> Edit</button>
                    {earlier.length > 0 && (
                      <select aria-label={`Restore an earlier version of ${row.prompt_name}`} value="" disabled={working}
                        onChange={(event) => event.target.value && rollBack(row, Number(event.target.value))}>
                        <option value="">Restore an earlier version...</option>
                        {earlier.map((item) => <option key={item.version} value={item.version}>v{item.version} · {item.updated_by || "unknown"} · {formatPromptTimestamp(item.updated_at)}</option>)}
                      </select>
                    )}
                  </>
                )}
              </footer>
            )}
            {earlier.length > 0 && (
              <details className="prompt-history">
                <summary>{earlier.length} earlier version{earlier.length === 1 ? "" : "s"}</summary>
                <ul>{earlier.map((item) => <li key={item.version}><strong>v{item.version}</strong> <span className="prompt-inactive">inactive</span> <span className="settings-muted">{item.updated_by || "unknown"} · {formatPromptTimestamp(item.updated_at)}</span></li>)}</ul>
              </details>
            )}
          </article>
        );
      })}
      {!loading && !loadError && !data.active.length && <p className="settings-muted">No prompts are configured yet.</p>}
    </section>
  );
}
