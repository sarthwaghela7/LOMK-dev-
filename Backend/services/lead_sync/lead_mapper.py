"""Pure mapping of sheet rows to FlintDesk lead fields (no I/O)."""
import hashlib
import re
from datetime import date

NEEDS_LINE = re.compile(r"^will your needs involve any of the following\s*:?\s*(.*)$", re.I)


def norm(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


# FlintDesk field -> accepted normalised header spellings.
FIELD_HEADERS = {
    "date_received": ["entry date"],
    "email": ["enter your email address"],
    "name": ["enter your full name"],
    "phone": ["enter your sms"],
    "client_type": ["appointment type"],
    "source": ["utm source"],
    "client_enquiry": ["give you detail requirement", "give your detail requirement", "give your detailed requirements"],
}
NOT_SELECTED = {"", "no", "n", "false", "0", "unchecked", "none", "n a", "na", "null"}
SELECTED = {"yes", "y", "true", "1", "checked", "x", "selected"}


MONTHS = {name.lower(): i for i, name in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}


class RowError(ValueError):
    pass


def locate_columns(header):
    """Map each wanted field to a column index by header text, not position."""
    # A header cell may hold several lines (the Needs columns list two options
    # separated by a ==== line), so every line is indexed on its own.
    index = {}
    for position, cell in enumerate(header):
        for line in str(cell).split("\n"):
            if norm(line):
                index.setdefault(norm(line), position)
    fields = {f: next((index[h] for h in names if h in index), None) for f, names in FIELD_HEADERS.items()}
    # Every "Will your needs involve any of the following: ..." column is a
    # needs column, whatever options it lists. A cell may hold several such
    # lines (one per option sharing the column), so it is read once, under the
    # first option's label; the cell value says which option was chosen.
    needs = []
    for position, cell in enumerate(header):
        for line in str(cell).split("\n"):
            found = NEEDS_LINE.match(line.strip())
            if found and norm(found[1]):
                needs.append((found[1].strip(), position))
                break
    missing = [f for f, i in fields.items() if i is None] + ([] if needs else ["Needs columns"])
    return fields, needs, missing


def parse_entry_date(raw):
    """Parse the sheet's DD/MM/YYYY (or ISO) text explicitly; never guess."""
    text = str(raw or "").strip()
    named = re.match(r"^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})(?:\s.*)?$", text)
    if named:  # older rows: "June 3, 2026 08:49"
        month = MONTHS.get(named[1].lower())
        if not month:
            raise RowError(f"Entry date {text!r} has an unknown month")
        try:
            return date(int(named[3]), month, int(named[2]))
        except ValueError:
            raise RowError(f"Entry date {text!r} is not a real date") from None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T].*)?$", text)
    if m:
        day, month, year = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T].*)?$", text)
        if not m:
            raise RowError(f"Entry date {text!r} is not DD/MM/YYYY")
        year, month, day = int(m[1]), int(m[2]), int(m[3])
    try:
        return date(year, month, day)
    except ValueError:
        raise RowError(f"Entry date {text!r} is not a real date") from None


def build_needs(cells):
    """cells: [(label, raw value)]. Only genuinely selected needs are listed."""
    parts = []
    for label, raw in cells:
        value = str(raw or "").strip()
        key = norm(value)
        if key in NOT_SELECTED:
            continue
        if label == "Other":
            parts.append("Other" if key in SELECTED or key == "other" else f"Other: {value}")
        else:
            parts.append(label if key in SELECTED else value)
    return ", ".join(parts)


def _cell(row, position):
    return str(row[position]).strip() if position is not None and position < len(row) else ""


def row_key(values):
    """Stable identity of a row: entry date + email + name + phone.

    Entry date alone is never enough (several leads arrive per day). Other
    columns are deliberately excluded so later edits to e.g. notes in the sheet
    do not look like a new lead.
    """
    material = "\x1f".join(norm(values.get(f)) for f in ("date_received", "email", "name", "phone"))
    return hashlib.sha256(material.encode()).hexdigest()


def identity(row, fields):
    return {f: _cell(row, i) for f, i in fields.items()}


def map_row(row, fields, needs):
    """Return (raw identity values, lead record, entry date) or raise RowError."""
    values = identity(row, fields)
    if not any(values.values()) and not any(_cell(row, i) for _, i in needs):
        return None  # blank spacer row
    entry = parse_entry_date(values["date_received"])
    record = {k: v for k, v in values.items() if v}
    record["date_received"] = entry.isoformat()
    needs_text = build_needs([(label, _cell(row, i)) for label, i in needs])
    if needs_text:
        record["needs"] = needs_text
    return values, record, entry
