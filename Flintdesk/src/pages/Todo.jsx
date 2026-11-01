import { useCallback, useEffect, useState } from "react";
import {
  createTodo,
  deleteTodo,
  getTodos,
  updateTodo,
  updateTodoStatus,
} from "../data/api.js";
import { formatDisplayDate } from "../data/displayDate.js";

const STATUSES = ["To Do", "In Progress", "Completed", "Blocker", "Dropped"];
const emptyForm = () => ({
  title: "",
  entry_date: new Date().toLocaleDateString("en-CA"),
  due_date: "",
  description: "",
});

function TodoCard({ todo, selected, moving, onSelect, onOpen }) {
  return (
    <button
      type="button"
      className={`todo-card ${selected ? "selected" : ""} ${moving ? "moving" : ""}`}
      onClick={onSelect}
      onDoubleClick={onOpen}
      aria-pressed={selected}
    >
      <strong>{todo.title}</strong>
      {todo.due_date && (
        <span className="todo-due">
          <i className="fa-regular fa-calendar" /> Due{" "}
          {formatDisplayDate(todo.due_date)}
        </span>
      )}
      {todo.description && (
        <span className="todo-description">{todo.description}</span>
      )}
    </button>
  );
}

function TodoDetailsModal({ todo, onClose, onDelete, onUpdated }) {
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  // The board column owns status and entry date is the immutable creation
  // date. These are the three existing task-detail fields users can edit.
  const [form, setForm] = useState(() => ({
    title: todo.title || "",
    due_date: todo.due_date || "",
    description: todo.description || "",
  }));
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState("");
  useEffect(() => {
    const closeOnEscape = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const remove = async () => {
    if (!window.confirm("Delete this to-do card?")) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await onDelete(todo);
    } catch (exception) {
      setDeleteError(exception.message);
    } finally {
      setDeleting(false);
    }
  };
  const save = async (event) => {
    event.preventDefault();
    if (!form.title.trim()) return setEditError("Task name is required.");
    setSaving(true); setEditError("");
    try {
      onUpdated(await updateTodo(todo.id, {
        title: form.title.trim(), due_date: form.due_date || null,
        description: form.description || null,
      }));
    } catch (exception) {
      setEditError(exception.message || "Could not update this task.");
    } finally { setSaving(false); }
  };

  return (
    <div
      className="modal-backdrop todo-modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <form
        className="modal-card todo-modal todo-details-modal"
        onSubmit={save}
        role="dialog"
        aria-modal="true"
        aria-labelledby="todo-details-title"
      >
        <div className="call-modal-header">
          <div>
            <span className="todo-details-status">{todo.status}</span>
            <h3 id="todo-details-title">{todo.title}</h3>
          </div>
          <button
            type="button"
            className="call-modal-close"
            onClick={onClose}
            aria-label="Close task details"
          >
            <i className="fa-solid fa-xmark" />
          </button>
        </div>
        <label>
          <span>Task name</span>
          <input className="calls-filter-input" value={form.title} onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))} />
        </label>
        <div className="todo-details-dates">
          <div>
            <i className="fa-regular fa-calendar-plus" />
            <span>
              Entry date<strong>{formatDisplayDate(todo.entry_date)}</strong>
            </span>
          </div>
          <div>
            <i className="fa-regular fa-calendar-check" />
            <span>
              Due date
              <input type="date" className="calls-filter-input" value={form.due_date} onChange={(event) => setForm((current) => ({ ...current, due_date: event.target.value }))} />
            </span>
          </div>
        </div>
        <section className="todo-details-description">
          <span>Description</span>
          <textarea value={form.description} onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))} placeholder="Add context, notes, or next steps..." />
        </section>
        {deleteError && (
          <p className="todo-details-delete-error" role="alert">
            {deleteError}
          </p>
        )}
        {editError && <p className="todo-details-delete-error" role="alert">{editError}</p>}
        <div className="todo-details-actions">
          <button type="submit" className="primary-button" disabled={saving || !form.title.trim()}>
            {saving ? "Saving..." : "Save changes"}
          </button>
          <button
            type="button"
            className="table-action danger"
            disabled={deleting}
            onClick={remove}
          >
            <i className="fa-regular fa-trash-can" />{" "}
            {deleting ? "Deleting..." : "Delete task"}
          </button>
        </div>
      </form>
    </div>
  );
}

function CreateTodoModal({ onClose, onCreated }) {
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const closeOnEscape = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const change = (field, value) =>
    setForm((current) => ({ ...current, [field]: value }));
  const submit = async (event) => {
    event.preventDefault();
    if (!form.title.trim()) return setError("Task name is required.");
    setSaving(true);
    setError("");
    try {
      const created = await createTodo({
        ...form,
        title: form.title.trim(),
        due_date: form.due_date || null,
        description: form.description || null,
      });
      onCreated(created);
    } catch (exception) {
      setError(exception.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="modal-backdrop todo-modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <form
        className="modal-card todo-modal todo-create-modal"
        onSubmit={submit}
      >
        <div className="call-modal-header">
          <div>
            <p className="todo-modal-eyebrow">Add to your board</p>
            <h3>Create a new task</h3>
            <p className="todo-modal-subtitle">
              Capture the details now and move the card as work progresses.
            </p>
          </div>
          <button
            type="button"
            className="call-modal-close"
            onClick={onClose}
            aria-label="Close create task modal"
          >
            <i className="fa-solid fa-xmark" />
          </button>
        </div>
        <label>
          <span>
            <i className="fa-solid fa-list-check" /> Task name
          </span>
          <input
            autoFocus
            className="calls-filter-input"
            value={form.title}
            onChange={(event) => change("title", event.target.value)}
            placeholder="What needs to be done?"
          />
        </label>
        <div className="todo-date-row">
          <label>
            <span>
              <i className="fa-regular fa-calendar-plus" /> Entry date
            </span>
            <input
              required
              type="date"
              className="calls-filter-input"
              value={form.entry_date}
              onChange={(event) => change("entry_date", event.target.value)}
            />
          </label>
          <label>
            <span>
              <i className="fa-regular fa-calendar-check" /> Due date{" "}
              <em>Optional</em>
            </span>
            <input
              type="date"
              className="calls-filter-input"
              value={form.due_date}
              onChange={(event) => change("due_date", event.target.value)}
            />
          </label>
        </div>
        <label>
          <span>
            <i className="fa-regular fa-note-sticky" /> Description{" "}
            <em>Optional</em>
          </span>
          <textarea
            value={form.description}
            onChange={(event) => change("description", event.target.value)}
            placeholder="Add context, notes, or next steps..."
          />
        </label>
        {error && (
          <p className="leads-error" role="alert">
            {error}
          </p>
        )}
        <div className="leads-modal-actions">
          <button type="button" className="table-action" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="primary-button"
            disabled={!form.title.trim() || saving}
          >
            {saving ? "Creating..." : "Create task"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function Todo() {
  const [todos, setTodos] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [movingId, setMovingId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [openedTodo, setOpenedTodo] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setTodos(await getTodos());
    } catch (exception) {
      setError(exception.message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const moveSelected = useCallback(
    async (direction) => {
      if (!selectedId || movingId) return;
      const selected = todos.find((todo) => todo.id === selectedId);
      const index = STATUSES.indexOf(selected?.status);
      const nextIndex = direction === "next" ? index + 1 : index - 1;
      if (!selected || nextIndex < 0 || nextIndex >= STATUSES.length) return;
      const target = STATUSES[nextIndex];
      setError("");
      setMovingId(selectedId);
      setTodos((current) =>
        current.map((todo) =>
          todo.id === selectedId ? { ...todo, status: target } : todo,
        ),
      );
      try {
        const saved = await updateTodoStatus(selectedId, target);
        setTodos((current) =>
          current.map((todo) => (todo.id === selectedId ? saved : todo)),
        );
      } catch (exception) {
        setTodos((current) =>
          current.map((todo) => (todo.id === selectedId ? selected : todo)),
        );
        setError(`Card could not be moved. ${exception.message}`);
      } finally {
        window.setTimeout(() => setMovingId(null), 220);
      }
    },
    [movingId, selectedId, todos],
  );

  const removeTodo = async (todo) => {
    setError("");
    setNotice("");
    try {
      await deleteTodo(todo.id);
      setTodos((current) => current.filter((item) => item.id !== todo.id));
      setSelectedId((current) => (current === todo.id ? null : current));
      setOpenedTodo(null);
      setNotice("Task deleted successfully.");
    } catch (exception) {
      setError(exception.message);
      throw exception;
    }
  };

  useEffect(() => {
    const handleKey = (event) => {
      if (
        modalOpen ||
        openedTodo ||
        !selectedId ||
        !["ArrowLeft", "ArrowRight"].includes(event.key)
      )
        return;
      event.preventDefault();
      moveSelected(event.key === "ArrowRight" ? "next" : "prev");
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [modalOpen, moveSelected, openedTodo, selectedId]);

  return (
    <section className="process-page todo-page">
      <div className="todo-heading">
        <h1>To-do</h1>
        <span className="todo-total">
          {todos.length} {todos.length === 1 ? "card" : "cards"}
        </span>
      </div>
      {error && (
        <div className="todo-alert error" role="alert">
          {error}
          <button type="button" onClick={load}>
            Try again
          </button>
        </div>
      )}
      {notice && (
        <div className="todo-alert success" role="status">
          {notice}
        </div>
      )}
      {loading ? (
        <div className="todo-loading">Loading your board...</div>
      ) : (
        <div className="todo-board">
          {STATUSES.map((status) => {
            const items = todos.filter((todo) => todo.status === status);
            return (
              <section
                className={`todo-column status-${status.toLowerCase().replaceAll(" ", "-")}`}
                key={status}
              >
                <header>
                  <h2>{status}</h2>
                  <span className="todo-count">{items.length}</span>
                </header>
                <div className="todo-card-list">
                  {items.map((todo) => (
                    <TodoCard
                      key={todo.id}
                      todo={todo}
                      selected={selectedId === todo.id}
                      moving={movingId === todo.id}
                      onSelect={() => setSelectedId(todo.id)}
                      onOpen={() => setOpenedTodo(todo)}
                    />
                  ))}
                  {!items.length && <p className="todo-empty">No cards</p>}
                </div>
              </section>
            );
          })}
        </div>
      )}
      <button
        type="button"
        className="todo-add-button"
        aria-label="Create a task"
        title="Create a task"
        onClick={() => {
          setNotice("");
          setModalOpen(true);
        }}
      >
        <i className="fa-solid fa-plus" />
      </button>
      {modalOpen && (
        <CreateTodoModal
          onClose={() => setModalOpen(false)}
          onCreated={(todo) => {
            setTodos((current) => [todo, ...current]);
            setSelectedId(todo.id);
            setModalOpen(false);
            setNotice("Task created successfully.");
          }}
        />
      )}
      {openedTodo && (
        <TodoDetailsModal
          todo={todos.find((todo) => todo.id === openedTodo.id) || openedTodo}
          onClose={() => setOpenedTodo(null)}
          onDelete={removeTodo}
          onUpdated={(saved) => {
            setTodos((current) => current.map((todo) => todo.id === saved.id ? saved : todo));
            setOpenedTodo(saved);
            setNotice("Task updated successfully.");
          }}
        />
      )}
    </section>
  );
}
