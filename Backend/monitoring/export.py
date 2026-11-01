"""CSV and Excel export. Never overwrites history, never duplicates a date silently."""
import csv
import shutil
from datetime import datetime
from pathlib import Path

from .report import COLUMNS

FORMULA_COLUMNS = ("Total Daily Cost (USD)", "Cost per STT-Completed Call (USD)")  # R, S in the tracker


def _cell(value):
    return "" if value is None else value


def write_csv(path, reports, *, replace_existing=False):
    """Append reports to ``path``. Returns (written_dates, skipped_dates)."""
    path = Path(path)
    existing = {}
    if path.exists():
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                existing[row["Date"]] = row
    written, skipped = [], []
    for report in reports:
        key = report.day.isoformat()
        if key in existing and not replace_existing:
            skipped.append(key)
            continue
        existing[key] = {c: _cell(report.values.get(c)) for c in COLUMNS}
        written.append(key)
    if written:
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            for key in sorted(existing):
                writer.writerow(existing[key])
        tmp.replace(path)  # atomic
    return written, skipped


def append_to_workbook(template, output, reports, *, replace_existing=False, in_place=False):
    """Append rows to the tracker's Daily Tracker sheet.

    Only input columns are written; the tracker's own formulas in R and S are left
    untouched. Empty values are left blank (a blank is not a zero). With
    replace_existing, a same-date row only has cells overwritten where the new
    value is not None, so manually entered figures are not erased.
    The template is never modified unless in_place (a timestamped backup is made first)."""
    import openpyxl
    template, output = Path(template), Path(output)
    if output.resolve() == template.resolve():
        if not in_place:
            raise ValueError("output equals the template; pass in_place=True to modify it (a backup is made)")
        shutil.copy2(template, template.with_name(f"{template.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}{template.suffix}"))
    wb = openpyxl.load_workbook(template)
    ws = wb["Daily Tracker"]
    header = [c.value for c in ws[1]]
    if header[:len(COLUMNS)] != COLUMNS:
        raise ValueError("Daily Tracker header does not match the expected 23 columns; refusing to write")
    col = {name: i + 1 for i, name in enumerate(COLUMNS)}
    dates = {}
    for r in range(2, ws.max_row + 1):
        value = ws.cell(r, 1).value
        if value not in (None, ""):
            dates[(value.date() if hasattr(value, "date") else value).isoformat()] = r
    next_row = (max(dates.values()) + 1) if dates else 2  # row after the last used one
    written, skipped = [], []
    for report in sorted(reports, key=lambda x: x.day):
        key = report.day.isoformat()
        if key in dates and not replace_existing:
            skipped.append(key)
            continue
        row = dates.get(key)
        if row is None:
            row = next_row
            next_row += 1
            dates[key] = row
            if row > 366:  # copy the formula pattern past the template's pre-built rows
                for name, letter in (("Total Daily Cost (USD)", "R"), ("Cost per STT-Completed Call (USD)", "S")):
                    ws.cell(row, col[name]).value = ws.cell(row - 1, col[name]).value.replace(str(row - 1), str(row))
        for name in COLUMNS:
            if name in FORMULA_COLUMNS:
                continue
            value = report.values.get(name)
            if name == "Date":
                value = datetime.combine(report.day, datetime.min.time())
            if value is None and ws.cell(row, col[name]).value not in (None, ""):
                continue  # keep any existing manual entry
            ws.cell(row, col[name]).value = value
        written.append(key)
    wb.save(output)
    return written, skipped
