/** Shared table viewport: use one native scrollbar for each scrolling axis. */
export function TableScrollWrap({ className = "", children }) {
  return (
    <div className="table-scroll-outer">
      <div className={className}>{children}</div>
    </div>
  );
}
