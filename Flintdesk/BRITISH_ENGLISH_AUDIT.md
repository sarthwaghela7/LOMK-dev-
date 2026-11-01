# FlintDesk British English audit

## Applied decisions

- UI headings and field labels use Title Case.
- Actions, buttons, toolbar controls, notices, and body text use sentence case.
- View Call displays `Analyse`, `Analysing...`, and `Re-analyse`; internal code identifiers remain unchanged.
- Stored client-type values containing `Organization` remain unchanged. A display mapping renders `Organisation` and corrects the inconsistent display capitalisation of `Existing client`.
- Task assignment copy uses `call assignment` consistently.
- Dependency UI and export headings use `Query`; the existing `question` database/API field remains unchanged.

## Standing open item: currency

Business Opportunity fees remain displayed with `$`. The fee table describes its values as dollars and contains no currency code. Do not replace the symbol or introduce a GBP formatter until the data owner explicitly confirms that the underlying amounts are GBP.

## Intentionally unchanged

- Database columns, API keys, enums, route names, and internal identifiers.
- `Enquiry Question`, which is an established field label.
- `Frontdesk Team` and `FlintDesk`, because they identify the team and product respectively.
