import { useCallback, useEffect, useMemo, useState } from "react";
import PromptManagementPanel from "../components/PromptManagementPanel.jsx";
import { addFDUser, addSyncFilter, assignExtension, deleteAssignment, deleteFDUser, deleteSyncFilter, getFDUsers, getSyncFilters, getTodayAssignments, updateFDUser, updateSyncFilter } from "../data/api.js";

// These are number-derived fallback values used for routing/filtering, not
// extensions that administrators need to configure or assign in Settings.
// These are routing aliases/trunks, not separate assignable calls.extension
// values.  The same public extension can otherwise appear once under its
// number and again under 601/603/605 in the assignment list.
const HIDDEN_SETTINGS_EXTENSIONS = new Set(["255", "4447", "601", "603", "605"]);
const HIDDEN_SETTINGS_PARENT_NUMBERS = new Set(["601", "603", "605"]);

export default function Settings({ isTeamLead = false, canEditSettings = false }) {
  const [filters, setFilters] = useState([]);
  const [agents, setAgents] = useState([]);
  const [assignments, setAssignments] = useState([]);
  const [numberDraft, setNumberDraft] = useState("");
  const [extensionDrafts, setExtensionDrafts] = useState({});
  const [agentDraft, setAgentDraft] = useState({ name: "", email: "", password: "" });
  const [reviewerDraft, setReviewerDraft] = useState({ name: "", email: "", password: "", role: "" });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editingUser, setEditingUser] = useState(null);
  const [userDraft, setUserDraft] = useState({ name: "", email: "", password: "" });
  const [editingFilter, setEditingFilter] = useState(null);
  const [filterDraft, setFilterDraft] = useState("");

  const loadFilters = useCallback(async () => {
    if (!isTeamLead) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const [savedFilters, savedAgents, savedAssignments] = await Promise.all([getSyncFilters(), getFDUsers(), getTodayAssignments()]);
      setFilters(savedFilters);
      setAgents(savedAgents);
      setAssignments(savedAssignments);
    } catch (loadError) {
      setError(loadError.message || "Unable to load sync filters.");
    } finally {
      setLoading(false);
    }
  }, [isTeamLead]);

  useEffect(() => {
    loadFilters();
  }, [loadFilters]);

  const groups = useMemo(() => {
    const unique = (rows, keyOf) => [...new Map(rows.map((row) => [keyOf(row), row])).values()];
    const numberRows = unique(filters.filter((item) => item.filter_type === "number"), (item) => item.value);
    return numberRows.map((number) => ({
      ...number,
      extensions: unique(
        filters.filter((item) => item.filter_type === "extension"
          && item.parent_number === number.value
          && !HIDDEN_SETTINGS_EXTENSIONS.has(String(item.value).trim())),
        (item) => `${item.parent_number}:${item.value}`,
      ),
    }));
  }, [filters]);
  const frontDeskAgents = useMemo(() => agents.filter((user) =>
    (user.role || "agent") === "agent"
  ), [agents]);
  const reviewers = useMemo(() => agents.filter((user) => ["team_lead", "subject_matter_expert"].includes(user.role)), [agents]);
  // Extension assignments belong only to the Front Desk agents shown above.
  const assignableUsers = useMemo(() => frontDeskAgents, [frontDeskAgents]);
  const configuredExtensions = useMemo(() => {
    const byExtension = new Map();
    groups
      .filter((group) => !HIDDEN_SETTINGS_PARENT_NUMBERS.has(String(group.value).trim()))
      .flatMap((group) => group.extensions.map((extension) => ({ ...extension, number: group.value })))
      .forEach((extension) => {
        const value = String(extension.value).trim();
        if (!value || HIDDEN_SETTINGS_EXTENSIONS.has(value)) return;
        // Keep the public-number configuration deterministically; a repeated
        // internal routing relationship never creates a second assignment UI.
        if (!byExtension.has(value)) byExtension.set(value, extension);
      });
    return [...byExtension.values()].sort((left, right) =>
      String(left.value).localeCompare(String(right.value), undefined, { numeric: true }),
    );
  }, [groups]);

  const addNumber = async () => {
    await addFilter("number", numberDraft, null, setNumberDraft);
  };

  const addExtension = async (parentNumber) => {
    const value = extensionDrafts[parentNumber] || "";
    await addFilter("extension", value, parentNumber, (nextValue) => {
      setExtensionDrafts((current) => ({ ...current, [parentNumber]: nextValue }));
    });
  };

  const addFilter = async (filterType, value, parentNumber, clear) => {
    const cleanValue = value.trim();
    if (!cleanValue) return;
    setSaving(true);
    setError("");
    try {
      const created = await addSyncFilter(filterType, cleanValue, parentNumber);
      setFilters((current) => [...current, created]);
      clear("");
    } catch (saveError) {
      setError(saveError.message || "Unable to save sync filter.");
    } finally {
      setSaving(false);
    }
  };

  const removeFilter = async (filterId) => {
    setSaving(true);
    setError("");
    try {
      await deleteSyncFilter(filterId);
      setFilters((current) => {
        const removed = current.find((item) => item.id === filterId);
        return current.filter((item) => item.id !== filterId && !(removed?.filter_type === "number" && item.parent_number === removed.value));
      });
    } catch (deleteError) {
      setError(deleteError.message || "Unable to delete sync filter.");
    } finally {
      setSaving(false);
    }
  };

  const addAgent = async () => {
    if (!agentDraft.name.trim() || !agentDraft.email.trim() || !agentDraft.password) return;
    setSaving(true);
    setError("");
    try {
      const created = await addFDUser(agentDraft.name, agentDraft.email, agentDraft.password, "agent");
      setAgents((current) => [...current, created].sort((a, b) => a.name.localeCompare(b.name)));
      setAgentDraft({ name: "", email: "", password: "" });
    } catch (agentError) {
      setError(agentError.message || "Unable to create front desk agent.");
    } finally {
      setSaving(false);
    }
  };

  const addReviewer = async () => {
    if (!reviewerDraft.name.trim() || !reviewerDraft.email.trim() || reviewerDraft.password.length < 8 || !reviewerDraft.role) return;
    setSaving(true);
    setError("");
    try {
      const created = await addFDUser(reviewerDraft.name, reviewerDraft.email, reviewerDraft.password, reviewerDraft.role);
      setAgents((current) => [...current, created].sort((a, b) => a.name.localeCompare(b.name)));
      setReviewerDraft({ name: "", email: "", password: "", role: "" });
    } catch (reviewerError) {
      setError(reviewerError.message || "Unable to create Team Lead or Subject Matter Expert.");
    } finally {
      setSaving(false);
    }
  };

  const removeAgent = async (userId) => {
    const person = agents.find((agent) => agent.id === userId);
    if (!window.confirm(`Remove ${person?.name || "this user"}? They will no longer be able to sign in.`)) return;
    setSaving(true);
    setError("");
    try {
      await deleteFDUser(userId);
      setAgents((current) => current.filter((agent) => agent.id !== userId));
      setNotice(`${person?.name || "User"} was removed.`);
    } catch (agentError) {
      setError(agentError.message || "Unable to remove front desk agent.");
    } finally {
      setSaving(false);
    }
  };

  const openUserEdit = (person) => {
    setEditingUser(person);
    setUserDraft({ name: person.name || "", email: person.email || "", password: "" });
    setError("");
  };

  const saveUserEdit = async (event) => {
    event.preventDefault();
    if (!editingUser) return;
    setSaving(true);
    setError("");
    try {
      const saved = await updateFDUser(editingUser.id, userDraft);
      setAgents((current) => current.map((person) => person.id === saved.id ? saved : person));
      setEditingUser(null);
      setNotice(`${saved.name}'s account details were saved.`);
    } catch (saveError) {
      setError(saveError.message || "Unable to save user details.");
    } finally {
      setSaving(false);
    }
  };

  const openFilterEdit = (filter) => {
    setEditingFilter(filter);
    setFilterDraft(filter.value || "");
    setError("");
  };

  const saveFilterEdit = async (event) => {
    event.preventDefault();
    if (!editingFilter) return;
    setSaving(true);
    setError("");
    try {
      const saved = await updateSyncFilter(editingFilter.id, filterDraft);
      await loadFilters();
      setEditingFilter(null);
      setNotice(`${saved.filter_type === "number" ? "Sync number" : "Extension"} ${saved.value} was saved.`);
    } catch (saveError) {
      setError(saveError.message || "Unable to save sync configuration.");
    } finally {
      setSaving(false);
    }
  };

  const saveAssignment = async (extension, agentId) => {
    setSaving(true);
    setError("");
    try {
      const currentAssignment = assignments.find((item) => item.extension === extension);
      if (!agentId) {
        if (currentAssignment) await deleteAssignment(currentAssignment.id);
        setAssignments((current) => current.filter((item) => item.extension !== extension));
        return;
      }
      const saved = await assignExtension(extension, agentId);
      setAssignments((current) => [...current.filter((item) => item.extension !== extension), saved]);
    } catch (assignmentError) {
      setError(assignmentError.message || "Unable to save extension assignment.");
    } finally {
      setSaving(false);
    }
  };

  const removeAssignment = async (assignmentId) => {
    setSaving(true);
    try {
      await deleteAssignment(assignmentId);
      setAssignments((current) => current.filter((item) => item.id !== assignmentId));
    } catch (assignmentError) {
      setError(assignmentError.message || "Unable to remove assignment.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="settings-page">
      <div className="dashboard-heading">
        <div>
          <h1>Settings</h1>
          <p>Manage the numbers and extensions F1 uses when syncing calls.</p>
        </div>
      </div>

      {notice && <p className="settings-success" role="status">{notice}</p>}
      {error && <p className="api-status settings-error-banner" role="alert">{error}</p>}
      {!canEditSettings && <p className="settings-readonly-note">Settings are view-only for your role.</p>}
      <div className="settings-readonly-shell">

      {isTeamLead && <section className="settings-card fd-agents-card">
        <div className="settings-card-heading">
          <div>
            <h2>Front Desk Agents</h2>
            <p>These accounts are stored in fd_users and can sign in to FlintDesk.</p>
          </div>
          <span className="settings-count">{frontDeskAgents.length}</span>
        </div>

        {loading ? (
          <p className="settings-muted">Loading agents...</p>
        ) : frontDeskAgents.length ? (
          <div className="fd-agent-list">
            {frontDeskAgents.map((agent) => (
              <div className="fd-agent-row" key={agent.id}>
                <div>
                  <strong>{agent.name}</strong>
                  <span>{agent.email}</span>
                </div>
                {canEditSettings && <div className="settings-row-actions">
                  <button type="button" className="icon-button" onClick={() => openUserEdit(agent)} disabled={saving} aria-label={`Edit ${agent.name}`} title="Edit agent"><i className="fa-solid fa-pen" /></button>
                  <button type="button" className="icon-button" onClick={() => removeAgent(agent.id)} disabled={saving} aria-label={`Remove ${agent.name}`} title="Remove agent">
                    <i className="fa-solid fa-trash" />
                  </button>
                </div>}
              </div>
            ))}
          </div>
        ) : (
          <p className="settings-muted">No front desk agents configured.</p>
        )}

        {canEditSettings && <form className="fd-agent-form" onSubmit={(event) => { event.preventDefault(); addAgent(); }}>
          <input type="text" value={agentDraft.name} onChange={(event) => setAgentDraft((current) => ({ ...current, name: event.target.value }))} placeholder="Full name" aria-label="Agent name" />
          <input type="email" value={agentDraft.email} onChange={(event) => setAgentDraft((current) => ({ ...current, email: event.target.value }))} placeholder="Email address" aria-label="Agent email" />
          <input type="password" minLength="8" value={agentDraft.password} onChange={(event) => setAgentDraft((current) => ({ ...current, password: event.target.value }))} placeholder="Password (8+ characters)" aria-label="Agent password" />
          <button type="submit" className="primary-button" disabled={saving || !agentDraft.name.trim() || !agentDraft.email.trim() || agentDraft.password.length < 8}>
            <i className="fa-solid fa-user-plus" /> Add Agent
          </button>
        </form>}
      </section>}

      {isTeamLead && <section className="settings-card fd-agents-card">
        <div className="settings-card-heading">
          <div>
            <h2>Team Leads / Subject Matter Experts</h2>
            <p>These accounts can manage dependencies and access FlintDesk features according to their assigned role.</p>
          </div>
          <span className="settings-count">{reviewers.length}</span>
        </div>

        {loading ? (
          <p className="settings-muted">Loading Team Leads and Subject Matter Experts...</p>
        ) : reviewers.length ? (
          <div className="fd-agent-list">
            {reviewers.map((reviewer) => (
              <div className="fd-agent-row" key={reviewer.id}>
                <div>
                  <strong>{reviewer.name}</strong>
                  <span>{reviewer.email}</span>
                  <span>{reviewer.role === "team_lead" ? "Team Lead" : "Subject Matter Expert"}</span>
                </div>
                {canEditSettings && <div className="settings-row-actions">
                  <button type="button" className="icon-button" onClick={() => openUserEdit(reviewer)} disabled={saving} aria-label={`Edit ${reviewer.name}`} title="Edit user"><i className="fa-solid fa-pen" /></button>
                  <button type="button" className="icon-button" onClick={() => removeAgent(reviewer.id)} disabled={saving} aria-label={`Remove ${reviewer.name}`} title="Remove user">
                    <i className="fa-solid fa-trash" />
                  </button>
                </div>}
              </div>
            ))}
          </div>
        ) : <p className="settings-muted">No Team Leads or Subject Matter Experts configured.</p>}

        {canEditSettings && <form className="fd-agent-form" onSubmit={(event) => { event.preventDefault(); addReviewer(); }}>
          <input type="text" value={reviewerDraft.name} onChange={(event) => setReviewerDraft((current) => ({ ...current, name: event.target.value }))} placeholder="Full name" aria-label="Reviewer name" />
          <input type="email" value={reviewerDraft.email} onChange={(event) => setReviewerDraft((current) => ({ ...current, email: event.target.value }))} placeholder="Email address" aria-label="Reviewer email" />
          <input type="password" minLength="8" value={reviewerDraft.password} onChange={(event) => setReviewerDraft((current) => ({ ...current, password: event.target.value }))} placeholder="Password (8+ characters)" aria-label="Reviewer password" />
          <select value={reviewerDraft.role} onChange={(event) => setReviewerDraft((current) => ({ ...current, role: event.target.value }))} aria-label="Reviewer role">
            <option value="">Select role</option>
            <option value="team_lead">Team Lead</option>
            <option value="subject_matter_expert">Subject Matter Expert</option>
          </select>
          <button type="submit" className="primary-button" disabled={saving || !reviewerDraft.name.trim() || !reviewerDraft.email.trim() || reviewerDraft.password.length < 8 || !reviewerDraft.role}>
            <i className="fa-solid fa-user-plus" /> Add User
          </button>
        </form>}
      </section>}

      {isTeamLead && <section className="settings-card assignment-card">
        <div className="settings-card-heading">
          <div>
            <h2>Extension Assignments</h2>
            <p>Assign each configured extension to an agent. The assignment remains active until it is changed or removed.</p>
          </div>
          <span className="settings-count">{configuredExtensions.length}</span>
        </div>
        <div className="assignment-list">
          {configuredExtensions.map((extension) => {
            const assignment = assignments.find((item) => item.extension === extension.value);
            return (
              <div className="assignment-row" key={`${extension.number}:${extension.value}`}>
                <div><strong>{extension.number}</strong><span>{extension.value}</span></div>
                {canEditSettings ? <select value={assignment?.agent_id || ""} onChange={(event) => saveAssignment(extension.value, event.target.value)} disabled={saving}>
                  <option value="">Unassigned</option>
                  {assignableUsers.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
                </select> : <span className="assignment-readonly-value">{assignment ? assignableUsers.find((agent) => agent.id === assignment.agent_id)?.name || "Assigned" : "Unassigned"}</span>}
                {canEditSettings && assignment && <button type="button" className="icon-button" onClick={() => removeAssignment(assignment.id)} disabled={saving} title="Remove assignment"><i className="fa-solid fa-trash" /></button>}
              </div>
            );
          })}
        </div>
      </section>}

      {isTeamLead && <section className="settings-card sync-groups-card">
        <div className="settings-card-heading">
          <div>
            <h2>F1 Sync Numbers and Extensions</h2>
            <p>Extensions are grouped under their parent number. Only saved groups are used for call syncing.</p>
          </div>
          <span className="settings-count">{groups.length}</span>
        </div>

        {loading ? (
          <p className="settings-muted">Loading saved groups...</p>
        ) : groups.length ? (
          <div className="sync-group-list">
            {groups.map((group) => (
              <SyncGroup
                key={group.id}
                group={group}
                draft={extensionDrafts[group.value] || ""}
                onDraftChange={(value) => setExtensionDrafts((current) => ({ ...current, [group.value]: value }))}
                onAddExtension={() => addExtension(group.value)}
                onDelete={removeFilter}
                onEdit={openFilterEdit}
                saving={saving}
                canEditSettings={canEditSettings}
              />
            ))}
          </div>
        ) : (
          <p className="settings-muted">No numbers configured.</p>
        )}

        {canEditSettings && <form className="sync-filter-form sync-number-form" onSubmit={(event) => { event.preventDefault(); addNumber(); }}>
          <input type="tel" value={numberDraft} onChange={(event) => setNumberDraft(event.target.value)} placeholder="Add a number, e.g. 2243374447" aria-label="Add sync number" />
          <button type="submit" className="primary-button" disabled={saving || !numberDraft.trim()}>
            <i className="fa-solid fa-plus" /> Add Number
          </button>
        </form>}
      </section>}

      <PromptManagementPanel canEdit={canEditSettings} />

      </div>

      {editingUser && <UserEditDialog user={editingUser} draft={userDraft} onChange={setUserDraft} onCancel={() => setEditingUser(null)} onSave={saveUserEdit} saving={saving} />}
      {editingFilter && <SyncFilterEditDialog filter={editingFilter} value={filterDraft} onChange={setFilterDraft} onCancel={() => setEditingFilter(null)} onSave={saveFilterEdit} saving={saving} />}

    </section>
  );
}

function SyncGroup({ group, draft, onDraftChange, onAddExtension, onDelete, onEdit, saving, canEditSettings }) {
  return (
    <article className="sync-group">
      <div className="sync-group-heading">
        <div>
          <span className="sync-group-label">Number</span>
          <strong>{group.value}</strong>
        </div>
        {canEditSettings && <div className="settings-row-actions"><button type="button" className="icon-button" onClick={() => onEdit(group)} disabled={saving} aria-label={`Edit number ${group.value}`} title="Edit sync number"><i className="fa-solid fa-pen" /></button><button type="button" className="icon-button" onClick={() => onDelete(group.id)} disabled={saving} aria-label={`Delete number ${group.value}`} title="Delete number and extensions"><i className="fa-solid fa-trash" /></button></div>}
      </div>

      <div className="sync-extension-section">
        <span className="sync-group-label">Extensions</span>
        {group.extensions.length ? (
          <div className="sync-extension-list">
            {group.extensions.map((extension) => (
              <div className="sync-extension-row" key={extension.id}>
                <span>{extension.value}</span>
                {canEditSettings && <div className="settings-row-actions"><button type="button" className="icon-button" onClick={() => onEdit(extension)} disabled={saving} aria-label={`Edit extension ${extension.value}`} title="Edit extension"><i className="fa-solid fa-pen" /></button><button type="button" className="icon-button" onClick={() => onDelete(extension.id)} disabled={saving} aria-label={`Delete extension ${extension.value}`} title="Delete extension"><i className="fa-solid fa-trash" /></button></div>}
              </div>
            ))}
          </div>
        ) : (
          <p className="settings-muted">No extensions configured.</p>
        )}
        {canEditSettings && <form className="sync-filter-form" onSubmit={(event) => { event.preventDefault(); onAddExtension(); }}>
          <input type="text" value={draft} onChange={(event) => onDraftChange(event.target.value)} placeholder="Add extension, e.g. 101" aria-label={`Add extension under ${group.value}`} />
          <button type="submit" className="primary-button" disabled={saving || !draft.trim()}>
            <i className="fa-solid fa-plus" /> Add Extension
          </button>
        </form>}
      </div>
    </article>
  );
}

function UserEditDialog({ user, draft, onChange, onCancel, onSave, saving }) {
  return (
    <div className="call-modal-overlay" role="presentation">
      <form className="call-modal-card settings-edit-dialog" onSubmit={onSave} role="dialog" aria-modal="true" aria-labelledby="edit-user-title">
        <header className="call-modal-header">
          <h2 id="edit-user-title">Edit {user.role === "agent" ? "Agent" : "User"}</h2>
          <button type="button" className="call-modal-close" onClick={onCancel} disabled={saving}>Close</button>
        </header>
        <div className="settings-edit-fields">
          <label>Name<input value={draft.name} onChange={(event) => onChange((current) => ({ ...current, name: event.target.value }))} required /></label>
          <label>Email<input type="email" value={draft.email} onChange={(event) => onChange((current) => ({ ...current, email: event.target.value }))} required /></label>
          <label>New password <span>(optional)</span><input type="password" minLength="8" value={draft.password} onChange={(event) => onChange((current) => ({ ...current, password: event.target.value }))} placeholder="Leave blank to keep current password" /></label>
          <p>Leaving the password blank keeps the existing password unchanged. The user role cannot be changed here.</p>
        </div>
        <footer className="call-modal-actions">
          <button type="button" className="table-action" onClick={onCancel} disabled={saving}>Cancel</button>
          <button type="submit" className="primary-button" disabled={saving || !draft.name.trim() || !draft.email.trim()}>{saving ? "Saving..." : "Save changes"}</button>
        </footer>
      </form>
    </div>
  );
}

function SyncFilterEditDialog({ filter, value, onChange, onCancel, onSave, saving }) {
  const label = filter.filter_type === "number" ? "F1 sync number" : "Extension";
  return (
    <div className="call-modal-overlay" role="presentation">
      <form className="call-modal-card settings-edit-dialog" onSubmit={onSave} role="dialog" aria-modal="true" aria-labelledby="edit-filter-title">
        <header className="call-modal-header">
          <h2 id="edit-filter-title">Edit {label}</h2>
          <button type="button" className="call-modal-close" onClick={onCancel} disabled={saving}>Close</button>
        </header>
        <div className="settings-edit-fields">
          <label>{label}<input value={value} onChange={(event) => onChange(event.target.value)} inputMode="numeric" required /></label>
          <p>{filter.filter_type === "number" ? "Saving keeps child extensions and existing agent assignments associated with this number." : "Saving preserves this extension's existing agent assignment."}</p>
        </div>
        <footer className="call-modal-actions">
          <button type="button" className="table-action" onClick={onCancel} disabled={saving}>Cancel</button>
          <button type="submit" className="primary-button" disabled={saving || !value.trim()}>{saving ? "Saving..." : "Save changes"}</button>
        </footer>
      </form>
    </div>
  );
}
