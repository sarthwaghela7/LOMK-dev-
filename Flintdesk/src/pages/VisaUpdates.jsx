import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  acknowledgeVisaUpdate,
  createVisaUpdate,
  deleteVisaUpdate,
  getVisaUpdate,
  getVisaUpdateCounts,
  getVisaUpdates,
} from "../data/api.js";
import { canManageVisaUpdates } from "../data/accessControl.js";
import { Pagination } from "../components/Pagination.jsx";
import { formatDisplayDate } from "../data/displayDate.js";

const PAGE_SIZE = 5;
const QUESTION_TYPES = [
  ["single_select", "Single Select"],
  ["multiple_select", "Multiple Select"],
  ["short_answer", "Short Answer"],
  ["true_false", "True / False"],
];
const emptyQuestion = () => ({
  question: "",
  question_type: "single_select",
  options: ["", ""],
  correct_answer: null,
  required: true,
});
const today = () => new Date().toLocaleDateString("en-CA");

function formatPostedDate(value) {
  return formatDisplayDate(value);
}

function inlineRichText(text) {
  const parts = String(text || "").split(
    /(\*\*[^*]+\*\*|\[[^\]]+\]\(https?:\/\/[^)]+\))/g,
  );
  return parts.filter(Boolean).map((part, index) => {
    const link = part.match(/^\[([^\]]+)]\((https?:\/\/[^)]+)\)$/);
    if (link)
      return (
        <a key={index} href={link[2]} target="_blank" rel="noopener noreferrer">
          {link[1]}
        </a>
      );
    if (part.startsWith("**") && part.endsWith("**"))
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    return <Fragment key={index}>{part}</Fragment>;
  });
}

function RichText({ value }) {
  const blocks = [];
  let list = [];
  let listType = "";
  const flushList = () => {
    if (!list.length) return;
    const Tag = listType === "ol" ? "ol" : "ul";
    blocks.push(
      <Tag key={`list-${blocks.length}`}>
        {list.map((item, index) => (
          <li key={index}>{inlineRichText(item)}</li>
        ))}
      </Tag>,
    );
    list = [];
    listType = "";
  };
  String(value || "")
    .split("\n")
    .forEach((line) => {
      const ordered = line.match(/^\s*\d+\.\s+(.+)/);
      const bullet = line.match(/^\s*[-*]\s+(.+)/);
      if (ordered || bullet) {
        const nextType = ordered ? "ol" : "ul";
        if (listType && listType !== nextType) flushList();
        listType = nextType;
        list.push((ordered || bullet)[1]);
        return;
      }
      flushList();
      if (line.startsWith("## "))
        blocks.push(
          <h3 key={blocks.length}>{inlineRichText(line.slice(3))}</h3>,
        );
      else if (line.startsWith("# "))
        blocks.push(
          <h2 key={blocks.length}>{inlineRichText(line.slice(2))}</h2>,
        );
      else if (line.trim())
        blocks.push(<p key={blocks.length}>{inlineRichText(line)}</p>);
    });
  flushList();
  return <div className="visa-rich-content">{blocks}</div>;
}

function VisaUpdateCard({ update, onOpen, canManage, onDelete }) {
  return (
    <article className={`visa-update-card ${update.read_status}`}>
      <div className="visa-update-card-accent" />
      <div className="visa-update-card-main">
        <div className="visa-update-card-meta">
          <span className="visa-case-badge">{update.case_type}</span>
          <span className={`visa-read-state ${update.read_status}`}>
            <i
              className={`fa-solid ${update.read_status === "read" ? "fa-check" : "fa-circle"}`}
            />{" "}
            {update.read_status}
          </span>
        </div>
        <h2>{update.title}</h2>
        <p>
          {update.short_description ||
            "Open this update to read the full details."}
        </p>
        <div className="visa-update-card-footer">
          <span>
            <i className="fa-regular fa-calendar" />{" "}
            {formatPostedDate(update.update_date)}
          </span>
          <div className="visa-update-card-footer-actions">
            {canManage && (
              <button
                type="button"
                className="visa-delete-button"
                onClick={() => onDelete(update.id)}
              >
                <i className="fa-solid fa-trash" /> Delete
              </button>
            )}
            <button type="button" onClick={() => onOpen(update.id)}>
              Read more <i className="fa-solid fa-arrow-right" />
            </button>
          </div>
        </div>
      </div>
    </article>
  );
}


function UpdateDetail({ updateId, onBack, onAcknowledged }) {
  const [update, setUpdate] = useState(null);
  const [answers, setAnswers] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    getVisaUpdate(updateId)
      .then((data) => {
        if (active) setUpdate(data);
      })
      .catch((exception) => {
        if (active) setError(exception.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [updateId]);
  const complete = useMemo(
    () =>
      update?.questions?.every(
        (question) =>
          !question.required ||
          (question.question_type === "multiple_select"
            ? answers[question.id]?.length
            : answers[question.id] !== undefined &&
              answers[question.id] !== ""),
      ),
    [answers, update],
  );
  const submit = async () => {
    setSaving(true);
    setError("");
    try {
      const result = await acknowledgeVisaUpdate(
        update.id,
        Object.entries(answers).map(([question_id, answer]) => ({
          question_id,
          answer,
        })),
      );
      setUpdate((current) => ({
        ...current,
        read_status: "read",
        read_at: result.read_at,
        questions: [],
      }));
      onAcknowledged();
    } catch (exception) {
      setError(exception.message);
    } finally {
      setSaving(false);
    }
  };
  if (loading)
    return (
      <div className="visa-page-state">
        <span className="visa-spinner" />
        Loading update…
      </div>
    );
  if (!update)
    return (
      <div className="visa-page-state error">
        <p>{error || "Update not found."}</p>
        <button className="table-action" onClick={onBack}>
          Back to updates
        </button>
      </div>
    );
  return (
    <section className="visa-detail-page">
      <button type="button" className="visa-back-button" onClick={onBack}>
        <i className="fa-solid fa-arrow-left" /> All visa updates
      </button>
      <article className="visa-detail-card">
        <header>
          <div>
            <span className="visa-case-badge">{update.case_type}</span>
            <h1>{update.title}</h1>
            <p>
              <i className="fa-regular fa-calendar" /> Published{" "}
              {formatPostedDate(update.update_date)}
            </p>
          </div>
          {update.read_status === "read" && (
            <span className="visa-complete-badge">
              <i className="fa-solid fa-check" /> Read
            </span>
          )}
        </header>
        <RichText value={update.details} />
        {update.reference_link && (
          <a
            className="visa-reference-link"
            href={update.reference_link}
            target="_blank"
            rel="noopener noreferrer"
          >
            <i className="fa-solid fa-arrow-up-right-from-square" />
            <span>
              <small>Reference link</small>View source information
            </span>
          </a>
        )}
      </article>
      {update.read_status === "unread" ? (
        <article className="visa-ack-card">
          <div className="visa-ack-heading">
            <span>
              <i className="fa-regular fa-circle-check" />
            </span>
            <div>
              <h2>
                {update.questions.length
                  ? "Confirm your understanding"
                  : "Acknowledge this update"}
              </h2>
              <p>
                {update.questions.length
                  ? "Complete the required questions before marking this update as read."
                  : "Please confirm that you have reviewed this update."}
              </p>
            </div>
          </div>
          {update.questions.map((question, index) => (
            <QuestionInput
              key={question.id}
              question={question}
              number={index + 1}
              value={answers[question.id]}
              onChange={(value) =>
                setAnswers((current) => ({ ...current, [question.id]: value }))
              }
            />
          ))}
          {error && (
            <p className="visa-form-error" role="alert">
              {error}
            </p>
          )}
          <div className="visa-ack-actions">
            <button
              type="button"
              className="primary-button"
              disabled={!complete || saving}
              onClick={submit}
            >
              {saving
                ? "Submitting…"
                : update.questions.length
                  ? "Submit and mark as read"
                  : "I have read it"}
            </button>
          </div>
        </article>
      ) : (
        <div className="visa-read-confirmation">
          <i className="fa-solid fa-circle-check" />
          <div>
            <strong>I have read it</strong>
            <span>
              Completed{" "}
              {update.read_at
                ? formatPostedDate(update.read_at.slice(0, 10))
                : ""}
            </span>
          </div>
        </div>
      )}
    </section>
  );
}

function QuestionInput({ question, number, value, onChange }) {
  return (
    <fieldset className="visa-reader-question">
      <legend>
        <span>{number}</span>
        {question.question}
        {question.required && <em>Required</em>}
      </legend>
      {question.question_type === "short_answer" && (
        <input
          className="calls-filter-input"
          value={value || ""}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Type your answer"
        />
      )}
      {question.question_type === "true_false" && (
        <div className="visa-answer-options">
          {[true, false].map((option) => (
            <label key={String(option)}>
              <input
                type="radio"
                name={question.id}
                checked={value === option}
                onChange={() => onChange(option)}
              />
              <span>{option ? "True" : "False"}</span>
            </label>
          ))}
        </div>
      )}
      {question.question_type === "single_select" && (
        <div className="visa-answer-options">
          {question.options.map((option) => (
            <label key={option}>
              <input
                type="radio"
                name={question.id}
                checked={value === option}
                onChange={() => onChange(option)}
              />
              <span>{option}</span>
            </label>
          ))}
        </div>
      )}
      {question.question_type === "multiple_select" && (
        <div className="visa-answer-options">
          {question.options.map((option) => (
            <label key={option}>
              <input
                type="checkbox"
                checked={(value || []).includes(option)}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...(value || []), option]
                      : (value || []).filter((item) => item !== option),
                  )
                }
              />
              <span>{option}</span>
            </label>
          ))}
        </div>
      )}
    </fieldset>
  );
}

function RichTextEditor({ value, onChange }) {
  const ref = useRef(null);
  const insert = (before, after = before, placeholder = "text") => {
    const element = ref.current;
    const start = element.selectionStart;
    const end = element.selectionEnd;
    const selected = value.slice(start, end) || placeholder;
    onChange(
      `${value.slice(0, start)}${before}${selected}${after}${value.slice(end)}`,
    );
    window.setTimeout(() => {
      element.focus();
      element.setSelectionRange(
        start + before.length,
        start + before.length + selected.length,
      );
    }, 0);
  };
  return (
    <div className="visa-rich-editor">
      <div className="visa-editor-toolbar">
        <button
          type="button"
          onClick={() => insert("**", "**", "bold text")}
          title="Bold"
        >
          <i className="fa-solid fa-bold" />
        </button>
        <button
          type="button"
          onClick={() => insert("## ", "", "Heading")}
          title="Heading"
        >
          <i className="fa-solid fa-heading" />
        </button>
        <button
          type="button"
          onClick={() => insert("- ", "", "List item")}
          title="Bullet list"
        >
          <i className="fa-solid fa-list-ul" />
        </button>
        <button
          type="button"
          onClick={() => insert("1. ", "", "List item")}
          title="Numbered list"
        >
          <i className="fa-solid fa-list-ol" />
        </button>
        <button
          type="button"
          onClick={() => insert("[", "](https://example.com)", "link text")}
          title="Link"
        >
          <i className="fa-solid fa-link" />
        </button>
      </div>
      <textarea
        ref={ref}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Write the complete visa or case update…"
      />
    </div>
  );
}

function CreateUpdate({ onCancel, onCreated }) {
  const [form, setForm] = useState({
    case_type: "",
    title: "",
    details: "",
    reference_link: "",
    update_date: today(),
    questions: [],
  });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const change = (field, value) =>
    setForm((current) => ({ ...current, [field]: value }));
  const updateQuestion = (index, field, value) =>
    change(
      "questions",
      form.questions.map((question, itemIndex) =>
        itemIndex === index ? { ...question, [field]: value } : question,
      ),
    );
  const valid =
    form.case_type.trim() &&
    form.title.trim() &&
    form.details.trim() &&
    form.update_date &&
    form.questions.every(
      (question) =>
        question.question.trim() &&
        (!question.question_type.includes("select") ||
          question.options.filter((option) => option.trim()).length >= 2),
    );
  const submit = async (event) => {
    event.preventDefault();
    if (!valid)
      return setError("Complete all required update and question fields.");
    setSaving(true);
    setError("");
    try {
      await createVisaUpdate({
        ...form,
        reference_link: form.reference_link || null,
        questions: form.questions.map((question) => ({
          ...question,
          options: question.question_type.includes("select")
            ? question.options.filter((option) => option.trim())
            : null,
        })),
      });
      onCreated();
    } catch (exception) {
      setError(exception.message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <section className="visa-create-page">
      <button type="button" className="visa-back-button" onClick={onCancel}>
        <i className="fa-solid fa-arrow-left" /> All visa updates
      </button>
      <form className="visa-create-card" onSubmit={submit}>
        <header>
          <span className="visa-create-icon">
            <i className="fa-solid fa-passport" />
          </span>
          <div>
            <h1>Create Visa Update</h1>
            <p>Publish important visa and case information for the team.</p>
          </div>
        </header>
        <div className="visa-form-grid">
          <label>
            <span>
              Mention Case Type <em>Required</em>
            </span>
            <input
              className="calls-filter-input"
              value={form.case_type}
              onChange={(event) => change("case_type", event.target.value)}
              placeholder="Example: H4 EAD"
            />
          </label>
          <label>
            <span>
              Date of Update <em>Required</em>
            </span>
            <input
              type="date"
              className="calls-filter-input"
              value={form.update_date}
              onChange={(event) => change("update_date", event.target.value)}
            />
          </label>
        </div>
        <label>
          <span>
            Mention Case Update Title <em>Required</em>
          </span>
          <input
            className="calls-filter-input"
            value={form.title}
            onChange={(event) => change("title", event.target.value)}
            placeholder="Enter a clear update title"
          />
        </label>
        <label>
          <span>
            Provide Update Details <em>Required</em>
          </span>
          <RichTextEditor
            value={form.details}
            onChange={(value) => change("details", value)}
          />
          <small>
            Use the toolbar for headings, bold text, lists, and safe links.
          </small>
        </label>
        <label>
          <span>
            Reference Link <em>Optional</em>
          </span>
          <input
            type="url"
            className="calls-filter-input"
            value={form.reference_link}
            onChange={(event) => change("reference_link", event.target.value)}
            placeholder="https://…"
          />
        </label>
        <section className="visa-question-builder">
          <div className="visa-question-builder-heading">
            <div>
              <h2>Questions for readers</h2>
              <p>
                Optional questions readers must complete before acknowledging
                the update.
              </p>
            </div>
            <button
              type="button"
              className="table-action"
              onClick={() =>
                change("questions", [...form.questions, emptyQuestion()])
              }
            >
              <i className="fa-solid fa-plus" /> Add question
            </button>
          </div>
          {form.questions.map((question, index) => (
            <div className="visa-question-editor" key={index}>
              <div className="visa-question-number">{index + 1}</div>
              <div className="visa-question-fields">
                <label>
                  <span>Question</span>
                  <input
                    className="calls-filter-input"
                    value={question.question}
                    onChange={(event) =>
                      updateQuestion(index, "question", event.target.value)
                    }
                    placeholder="What should the reader confirm?"
                  />
                </label>
                <div className="visa-form-grid">
                  <label>
                    <span>Answer type</span>
                    <select
                      className="calls-filter-select"
                      value={question.question_type}
                      onChange={(event) =>
                        change(
                          "questions",
                          form.questions.map((item, itemIndex) =>
                            itemIndex === index
                              ? {
                                  ...item,
                                  question_type: event.target.value,
                                  correct_answer: null,
                                }
                              : item,
                          ),
                        )
                      }
                    >
                      {QUESTION_TYPES.map(([value, label]) => (
                        <option value={value} key={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="visa-required-toggle">
                    <input
                      type="checkbox"
                      checked={question.required}
                      onChange={(event) =>
                        updateQuestion(index, "required", event.target.checked)
                      }
                    />
                    <span>Required response</span>
                  </label>
                </div>
                {question.question_type.includes("select") && (
                  <label>
                    <span>Answer options</span>
                    <div className="visa-option-list">
                      {question.options.map((option, optionIndex) => (
                        <div key={optionIndex}>
                          <input
                            className="calls-filter-input"
                            value={option}
                            onChange={(event) =>
                              updateQuestion(
                                index,
                                "options",
                                question.options.map((item, itemIndex) =>
                                  itemIndex === optionIndex
                                    ? event.target.value
                                    : item,
                                ),
                              )
                            }
                            placeholder={`Option ${optionIndex + 1}`}
                          />
                          {question.options.length > 2 && (
                            <button
                              type="button"
                              onClick={() =>
                                updateQuestion(
                                  index,
                                  "options",
                                  question.options.filter(
                                    (_, itemIndex) => itemIndex !== optionIndex,
                                  ),
                                )
                              }
                              aria-label="Remove option"
                            >
                              <i className="fa-solid fa-xmark" />
                            </button>
                          )}
                        </div>
                      ))}
                      <button
                        type="button"
                        className="visa-add-option"
                        onClick={() =>
                          updateQuestion(index, "options", [
                            ...question.options,
                            "",
                          ])
                        }
                      >
                        + Add option
                      </button>
                    </div>
                  </label>
                )}
                <CorrectAnswerField
                  question={question}
                  onChange={(value) =>
                    updateQuestion(index, "correct_answer", value)
                  }
                />
              </div>
              <button
                type="button"
                className="visa-remove-question"
                onClick={() =>
                  change(
                    "questions",
                    form.questions.filter(
                      (_, itemIndex) => itemIndex !== index,
                    ),
                  )
                }
                aria-label={`Remove question ${index + 1}`}
              >
                <i className="fa-regular fa-trash-can" />
              </button>
            </div>
          ))}
          {!form.questions.length && (
            <div className="visa-no-questions">
              <i className="fa-regular fa-message" />
              <span>No acknowledgement questions added</span>
            </div>
          )}
        </section>
        {error && (
          <p className="visa-form-error" role="alert">
            {error}
          </p>
        )}
        <footer>
          <button type="button" className="table-action" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="submit"
            className="primary-button"
            disabled={!valid || saving}
          >
            {saving ? "Publishing…" : "Publish update"}
          </button>
        </footer>
      </form>
    </section>
  );
}

function CorrectAnswerField({ question, onChange }) {
  if (question.question_type === "single_select")
    return (
      <label>
        <span>
          Correct answer <em className="visa-optional-label">Optional</em>
        </span>
        <select
          className="calls-filter-select"
          value={question.correct_answer || ""}
          onChange={(event) => onChange(event.target.value || null)}
        >
          <option value="">Not configured</option>
          {question.options.filter(Boolean).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
    );
  if (question.question_type === "true_false")
    return (
      <label>
        <span>
          Correct answer <em className="visa-optional-label">Optional</em>
        </span>
        <select
          className="calls-filter-select"
          value={
            question.correct_answer === null
              ? ""
              : String(question.correct_answer)
          }
          onChange={(event) =>
            onChange(
              event.target.value === "" ? null : event.target.value === "true",
            )
          }
        >
          <option value="">Not configured</option>
          <option value="true">True</option>
          <option value="false">False</option>
        </select>
      </label>
    );
  return (
    <label>
      <span>
        Correct answer <em className="visa-optional-label">Optional</em>
      </span>
      <input
        className="calls-filter-input"
        value={
          Array.isArray(question.correct_answer)
            ? question.correct_answer.join(", ")
            : question.correct_answer || ""
        }
        onChange={(event) =>
          onChange(
            question.question_type === "multiple_select"
              ? event.target.value
                  .split(",")
                  .map((value) => value.trim())
                  .filter(Boolean)
              : event.target.value || null,
          )
        }
        placeholder={
          question.question_type === "multiple_select"
            ? "Separate multiple answers with commas"
            : "Enter the expected answer"
        }
      />
    </label>
  );
}

export default function VisaUpdates({ user }) {
  const [view, setView] = useState({ name: "list", id: null });
  const [page, setPage] = useState(1);
  const [data, setData] = useState({ items: [], total_pages: 1, total: 0 });
  const [counts, setCounts] = useState({ read: 0, unread: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [updates, nextCounts] = await Promise.all([
        getVisaUpdates(page, PAGE_SIZE),
        getVisaUpdateCounts(),
      ]);
      setData(updates);
      setCounts(nextCounts);
    } catch (exception) {
      setError(exception.message);
    } finally {
      setLoading(false);
    }
  }, [page]);
  useEffect(() => {
    if (view.name === "list") load();
  }, [load, view.name]);
  const handleDelete = async (updateId) => {
    if (!window.confirm("Are you sure you want to delete this visa update?"))
      return;
    setError("");
    try {
      await deleteVisaUpdate(updateId);
      setNotice("Visa update deleted.");
      if (data.items.length === 1 && page > 1)
        setPage((current) => current - 1);
      else await load();
    } catch (exception) {
      setError(exception.message);
    }
  };
  if (view.name === "detail")
    return (
      <UpdateDetail
        updateId={view.id}
        onBack={() => setView({ name: "list", id: null })}
        onAcknowledged={() => {
          setNotice("Update marked as read.");
          setCounts((current) => ({
            read: current.read + 1,
            unread: Math.max(0, current.unread - 1),
          }));
        }}
      />
    );
  if (view.name === "create" && canManageVisaUpdates(user))
    return (
      <CreateUpdate
        onCancel={() => setView({ name: "list", id: null })}
        onCreated={() => {
          setPage(1);
          setNotice("Visa update published successfully.");
          setView({ name: "list", id: null });
        }}
      />
    );
  return (
    <section className="visa-updates-page">
      <header className="visa-list-header">
        <div>
          <span className="visa-page-kicker">Knowledge centre</span>
          <h1>Visa Updates</h1>
          <p>Important visa and case updates for your team.</p>
        </div>
        <div className="visa-list-header-actions">
          <div className="visa-counter unread">
            <strong>{counts.unread}</strong>
            <span>Unread</span>
          </div>
          <div className="visa-counter">
            <strong>{counts.read}</strong>
            <span>Read</span>
          </div>
          {canManageVisaUpdates(user) && (
            <button
              type="button"
              className="primary-button"
              onClick={() => setView({ name: "create", id: null })}
            >
              Add update <i className="fa-solid fa-plus" />
            </button>
          )}
        </div>
      </header>
      {notice && (
        <div className="visa-notice">
          <i className="fa-solid fa-circle-check" />
          {notice}
          <button onClick={() => setNotice("")} aria-label="Dismiss">
            <i className="fa-solid fa-xmark" />
          </button>
        </div>
      )}
      {error && (
        <div className="visa-page-state error">
          <p>{error}</p>
          <button type="button" className="table-action" onClick={load}>
            Try again
          </button>
        </div>
      )}
      {loading ? (
        <div className="visa-page-state">
          <span className="visa-spinner" />
          Loading visa updates…
        </div>
      ) : data.items.length ? (
        <div className="visa-update-list">
          {data.items.map((update) => (
            <VisaUpdateCard
              key={update.id}
              update={update}
              onOpen={(id) => setView({ name: "detail", id })}
              canManage={canManageVisaUpdates(user)}
              onDelete={handleDelete}
            />
          ))}
        </div>
      ) : (
        <div className="visa-empty-state">
          <span>
            <i className="fa-regular fa-newspaper" />
          </span>
          <h2>No visa updates available</h2>
          <p>New visa and case updates will appear here.</p>
        </div>
      )}
      <Pagination
        ariaLabel="Visa updates pages"
        page={page}
        totalPages={data.total_pages}
        onChange={setPage}
      />
    </section>
  );
}
