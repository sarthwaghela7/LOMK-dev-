import { columnTags } from "../data/callStageColumns.js";

// A Call Records column header: its label plus the small "AI analysed" / "Optional" note under it.
export const ColumnHeaderLabel = ({ field }) => (
  <>
    {field.label}
    {columnTags(field.key).map((tag) => (
      <small key={tag} className={`calls-column-tag ${tag === "Optional" ? "calls-column-tag-optional" : "calls-column-tag-ai"}`}>{tag}</small>
    ))}
  </>
);

export default ColumnHeaderLabel;
