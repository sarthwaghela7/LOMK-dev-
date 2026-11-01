import { useState, useEffect } from "react";
import { getPrompts, createPrompt, updatePrompt, deletePrompt } from "../data/api";
import { Plus, Trash2, Eye, X, Pencil, ChevronUp, ChevronDown } from "lucide-react";

function ViewModal({ title, content, onClose }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{title}</h3>
          <button className="modal-close" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="modal-body">
          <pre className="modal-pre">{content || <span className="text-muted">— empty —</span>}</pre>
        </div>
      </div>
    </div>
  );
}

function PromptFormModal({ initial, onSave, onClose }) {
  const [form, setForm] = useState({
    key: initial?.key || "",
    description: initial?.description || "",
    notes: initial?.notes || "",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.key.trim()) { setError("Key is required."); return; }
    setLoading(true);
    setError("");
    try {
      await onSave(form);
      onClose();
    } catch (err) {
      setError(err.message || "Failed to save.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card modal-card-wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{initial ? "Edit Prompt" : "New Prompt"}</h3>
          <button className="modal-close" onClick={onClose}><X size={18} /></button>
        </div>
        <form onSubmit={handleSubmit} className="modal-form">
          {error && <div className="alert alert-error"><span>{error}</span></div>}
          <div className="form-field">
            <label className="form-label">Key</label>
            <input
              className="form-input"
              style={{ paddingLeft: "14px" }}
              type="text"
              placeholder="e.g. call_type"
              value={form.key}
              onChange={(e) => setForm((p) => ({ ...p, key: e.target.value }))}
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label">Description</label>
            <textarea
              className="form-textarea"
              placeholder="Enter description..."
              value={form.description}
              onChange={(e) => setForm((p) => ({ ...p, description: e.target.value }))}
              rows={5}
            />
          </div>
          <div className="form-field">
            <label className="form-label">Notes</label>
            <textarea
              className="form-textarea"
              placeholder="Enter notes..."
              value={form.notes}
              onChange={(e) => setForm((p) => ({ ...p, notes: e.target.value }))}
              rows={4}
            />
          </div>
          <div className="form-actions">
            <button type="submit" className="btn-primary" disabled={loading}>
              {loading ? "Saving..." : initial ? "Save Changes" : "Create Prompt"}
            </button>
            <button type="button" className="btn-secondary" onClick={onClose} disabled={loading}>Cancel</button>
          </div>
        </form>
      </div>
    </div>
  );
}

function AnalysisManagement() {
  const [prompts, setPrompts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [viewModal, setViewModal] = useState(null); // { title, content }
  const [formModal, setFormModal] = useState(null); // null | { prompt } | {}
  const [sortField, setSortField] = useState("id");
  const [sortDir, setSortDir] = useState("asc");

  const fetchPrompts = async () => {
    setLoading(true);
    try {
      const data = await getPrompts();
      setPrompts(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || "Failed to load prompts.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchPrompts(); }, []);

  const handleCreate = async (form) => {
    await createPrompt(form);
    await fetchPrompts();
  };

  const handleUpdate = async (id, form) => {
    await updatePrompt(id, form);
    await fetchPrompts();
  };

  const handleDelete = async (id, key) => {
    if (!window.confirm(`Delete prompt "${key}"? This will affect the LLM pipeline.`)) return;
    try {
      await deletePrompt(id);
      await fetchPrompts();
    } catch (err) {
      setError(err.message || "Failed to delete.");
    }
  };

  const handleSort = (field) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("asc");
    }
  };

  const sorted = [...prompts].sort((a, b) => {
    const av = sortField === "id" ? a.id : (a[sortField] || "").toLowerCase();
    const bv = sortField === "id" ? b.id : (b[sortField] || "").toLowerCase();
    if (av < bv) return sortDir === "asc" ? -1 : 1;
    if (av > bv) return sortDir === "asc" ? 1 : -1;
    return 0;
  });

  // Sequential display numbers (1, 2, 3, ...) based on row position,
  // so deleting a prompt doesn't leave a gap like 14, 16, 17.
  const displayNumbers = new Map(sorted.map((p, idx) => [p.id, idx + 1]));

  const SortIcon = ({ field }) => {
    if (sortField !== field) return null;
    return sortDir === "asc" ? <ChevronUp size={13} /> : <ChevronDown size={13} />;
  };

  const truncate = (str, n = 60) => {
    if (!str) return <span className="text-muted">—</span>;
    return str.length > n ? str.slice(0, n) + "…" : str;
  };

  return (
    <div className="analysis-mgmt-page">
      <div className="page-header">
        <div>
          <h1>Analysis Settings</h1>
          <p className="page-subtitle">Manage the LLM prompt instructions loaded at pipeline runtime.</p>
        </div>
        <button className="btn-primary" onClick={() => setFormModal({})}>
          <Plus size={15} /> New Prompt
        </button>
      </div>

      {error && (
        <div className="alert alert-error" style={{ marginBottom: 20 }}>
          <span>{error}</span>
          <button style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer", color: "inherit" }} onClick={() => setError("")}><X size={16} /></button>
        </div>
      )}

      <div className="table-container">
        {loading ? (
          <div className="table-empty">Loading prompts…</div>
        ) : sorted.length === 0 ? (
          <div className="table-empty">
            <p>No prompts found.</p>
            <button className="btn-primary" onClick={() => setFormModal({})}>
              <Plus size={15} /> Create your first prompt
            </button>
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th className="th-sortable" onClick={() => handleSort("id")} style={{ width: 60, textAlign: "center" }}>
                  <span className="th-sortable-inner" style={{ justifyContent: "center" }}>S.No <SortIcon field="id" /></span>
                </th>
                <th className="th-sortable" onClick={() => handleSort("key")} style={{ width: 190 }}>
                  <span className="th-sortable-inner">Key <SortIcon field="key" /></span>
                </th>
                <th>Description</th>
                <th>Notes</th>
                <th style={{ width: 100, textAlign: "center" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((prompt) => (
                <tr key={prompt.id}>
                  <td className="td-id" style={{ textAlign: "center" }}>{displayNumbers.get(prompt.id)}</td>
                  <td className="td-key"><code className="code-key">{prompt.key}</code></td>
                  <td>
                    <div className="td-preview-row">
                      <span className="td-preview">{truncate(prompt.description)}</span>
                      {prompt.description && (
                        <button
                          className="btn-view"
                          onClick={() => setViewModal({ title: `Description — ${prompt.key}`, content: prompt.description })}
                        >
                          <Eye size={13} /> View
                        </button>
                      )}
                    </div>
                  </td>
                  <td>
                    <div className="td-preview-row">
                      <span className="td-preview">{truncate(prompt.notes)}</span>
                      {prompt.notes && (
                        <button
                          className="btn-view"
                          onClick={() => setViewModal({ title: `Notes — ${prompt.key}`, content: prompt.notes })}
                        >
                          <Eye size={13} /> View
                        </button>
                      )}
                    </div>
                  </td>
                  <td>
                    <div className="td-actions" style={{ justifyContent: "center" }}>
                      <button
                        className="btn-icon-sm"
                        title="Edit"
                        onClick={() => setFormModal({ prompt })}
                      >
                        <Pencil size={14} />
                      </button>
                      {prompt.key !== "SYSTEM_INSTRUCTIONS" && (
                        <button
                          className="btn-icon-sm danger"
                          title="Delete"
                          onClick={() => handleDelete(prompt.id, prompt.key)}
                        >
                          <Trash2 size={14} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* View modal */}
      {viewModal && (
        <ViewModal
          title={viewModal.title}
          content={viewModal.content}
          onClose={() => setViewModal(null)}
        />
      )}

      {/* Create / Edit modal */}
      {formModal !== null && (
        <PromptFormModal
          initial={formModal.prompt || null}
          onSave={formModal.prompt
            ? (form) => handleUpdate(formModal.prompt.id, form)
            : handleCreate
          }
          onClose={() => setFormModal(null)}
        />
      )}
    </div>
  );
}

export default AnalysisManagement;