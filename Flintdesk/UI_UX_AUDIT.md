# FlintDesk UI/UX consistency audit

## Audited pages and components

- Dashboard: filters, KPI/table layouts, Lead Score Matrix modal.
- Calls: priority view, sticky columns, stage editors, transcript/audio/cell/client/lead modals.
- Leads: priority view, import/edit/transcript modals, filters, wide stage table.
- Tasks: general and call-assignment tables, import modal, filters and actions.
- Dependencies: create/edit/client/lead modals, filters and table.
- Case Closing: priority view, stage table, transcript/audio/cell modals.
- Follow Ups: filter belt and table.
- Settings: forms, assignment controls and cards.
- Login and application shell: buttons, profile and notification overlays.

## Issues found and resolved

- Priority details were absolutely positioned inside scrollable tables, allowing table overflow and sticky cells to clip or cover them. Lead Score and Business Opportunity details now open in a body-level modal with viewport-aware sizing, internal scrolling, backdrop close and Escape-key close.
- Modal implementations had different viewport units, padding and overflow ownership. Shared rules now enforce bounded dynamic sizing, one internal scroll area, stable scrollbars and mobile-safe top alignment.
- Modal headers and footers could shrink or collide with long content. They are fixed within the modal flow while content scrolls.
- Tables had competing cell padding and sizing rules. Shared final rules align cells vertically and apply consistent header/body spacing while retaining page-specific widths and horizontal scrolling.
- Filters and common actions now share bottom alignment, spacing, box sizing and minimum interactive height.
- Priority detail dialogs now sit above tables, sticky headers and existing overlays.

## Responsive QA targets

- Desktop: 1440 × 900 and larger.
- Laptop: 1280 × 720 and 1366 × 768.
- Tablet: 768 × 1024 and 1024 × 768.
- Narrow viewport: 390 × 844 for overflow and modal safety.

Automated lint and production-build checks cover compilation and static regressions. Interactive screenshots require authenticated FlintDesk data and should be captured against the QA deployment at the viewport sizes above.
