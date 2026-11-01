// Read-only "Case Type View with Enquiry Questions" table: one row group per week, one row per case type that
// actually occurred that week (the types come from the AI analysis, never from a fixed list). Rows come from the
// server (`caseTypeEnquiryRows`), built with the same rules as the Trends view and its question popup.
// A question asked by several calls is shown once with its count. A week with no enquiry questions says so.
export function CaseTypeEnquiryTable({ rows }) {
  return (
    <div className="case-types-eq-table-scroll">
      <table className="date-category-table case-type-enquiry-table case-types-eq-table">
        <thead>
          <tr>
            <th>Month &amp; Date</th>
            <th>Visa Type</th>
            <th>Enquiry Question</th>
          </tr>
        </thead>
        <tbody>
          {rows.flatMap((week) => {
            const types = week.types?.length ? week.types : [null];
            return types.map((item, index) => (
              <tr key={`${week.key}-${item ? item.type : "none"}`}>
                {index === 0 && (
                  <td rowSpan={types.length} className="case-type-week-cell">
                    <strong>{week.label}</strong>
                    <span>{week.range}</span>
                  </td>
                )}
                <td className="case-type-visa-cell">{item ? item.type : <span>—</span>}</td>
                <td className="case-type-questions-cell">
                  {item && item.questions.length ? (
                    (item.items || item.questions.map((text) => ({ text, count: 1 }))).map((question) => (
                      <div key={question.text}>
                        {question.text}
                        {question.count > 1 && <small className="case-type-question-count" title={`Asked by ${question.count} calls`}> ×{question.count}</small>}
                      </div>
                    ))
                  ) : (
                    <span>—</span>
                  )}
                </td>
              </tr>
            ));
          })}
        </tbody>
      </table>
    </div>
  );
}
