import { useCallback, useLayoutEffect, useRef, useState } from "react";

/**
 * Keeps existing filter markup/functionality intact while exposing a hint only
 * when the actual scroll container has content outside its visible viewport.
 */
export function ScrollableFilterBelt({ className = "", children }) {
  const beltRef = useRef(null);
  const [overflows, setOverflows] = useState(false);

  useLayoutEffect(() => {
    const belt = beltRef.current;
    if (!belt) return undefined;

    const measure = () => {
      setOverflows(belt.scrollWidth > belt.clientWidth + 1);
    };
    measure();

    const resizeObserver = new ResizeObserver(measure);
    const mutationObserver = new MutationObserver(measure);
    resizeObserver.observe(belt);
    mutationObserver.observe(belt, { childList: true, subtree: true });
    window.addEventListener("resize", measure);

    return () => {
      resizeObserver.disconnect();
      mutationObserver.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  return (
    <div className="filter-belt-container">
      {overflows && (
        <p className="filter-belt-scroll-guidance" role="status">
          <span aria-hidden="true">→</span> Scroll horizontally for more filters
        </p>
      )}
      <div ref={beltRef} className={`calls-filter-belt ${className}`.trim()}>
        {children}
      </div>
    </div>
  );
}

/**
 * Groups a filter belt with its action row so both stay pinned to the top of
 * the scrolling content area (see .sticky-filter-region in App.css).
 */
// The table box never gets shorter than this while pinned, plus room for the pagination row and page padding below it.
const MIN_PINNED_TABLE_PX = 520;
const PINNED_TABLE_RESERVE_PX = 100;

export function StickyFilterRegion({ className = "", children }) {
  // Publish the pinned region's height on its parent so sticky table headers
  // below it can pin underneath it instead of sliding behind it.
  const regionRef = useCallback((region) => {
    const parent = region?.parentElement;
    if (!parent) return undefined;
    // The pinned filters and the table's own pinned box must both fit under the top bar. When they do not (a short window, or
    // filters that wrap onto several rows), the filters stop being pinned (data-sticky-fits="no"; see flintdesk-theme.css) so
    // the table header can pin right under the top bar instead of being pushed behind it at the last rows.
    const publish = () => {
      parent.style.setProperty("--sticky-filter-h", `${region.offsetHeight}px`);
      const topBar = document.querySelector(".topbar");
      const available = window.innerHeight - (topBar ? topBar.getBoundingClientRect().bottom : 0);
      parent.dataset.stickyFits = region.offsetHeight + MIN_PINNED_TABLE_PX + PINNED_TABLE_RESERVE_PX <= available ? "yes" : "no";
    };
    publish();
    window.addEventListener("resize", publish);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(publish);
    observer?.observe(region);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", publish);
      parent.style.removeProperty("--sticky-filter-h");
      delete parent.dataset.stickyFits;
    };
  }, []);
  return <div ref={regionRef} className={`sticky-filter-region ${className}`.trim()}>{children}</div>;
}
