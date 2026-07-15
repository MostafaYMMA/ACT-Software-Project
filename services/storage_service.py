import sqlite3
import csv
import os
import re
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side
from openpyxl.worksheet.table import Table, TableStyleInfo

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "cards.db")

_HEADER_FONT = Font(bold=True, size=13, color="000000")
_HEADER_FILL = PatternFill(start_color="ADD8E6", end_color="ADD8E6", fill_type="solid")
_BODY_FONT = Font(color="000000")
_THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)

_DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_PERIOD_PATTERN = re.compile(
    r"from\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE
)

# --- ACT "Invoice Overview per Period" template constants -------------
_ACT_HEADERS = [
    "Date", "Invoice No", "Project Name", "Period", "Task Name", "Project Mgr",
    "PO", "SOW", "Line", "Type", "Project Number", "Consultant", "Qty",
    "Sales Price", "Total Amount",
]
_ACT_TITLE = "Advanced Computer Technology (Middle East) LABOR and EXPENSE Overview per Period"
_ACT_PO = "AE110007829"
_AED_FORMAT = (
    '_([$AED]\\ * #,##0.00_);_([$AED]\\ * \\(#,##0.00\\);'
    '_([$AED]\\ * "-"??_);_(@_)'
)


def _ensure_columns(conn, table, column_defs):
    """
    Adds any column in column_defs (list of "name TYPE" strings) that
    doesn't already exist on `table`. Lets older databases created before
    a schema change pick up new columns without losing existing rows.
    """
    existing = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    for column_def in column_defs:
        if column_def.startswith('"'):
            column_name = column_def[1:column_def.index('"', 1)]
        else:
            column_name = column_def.split()[0]
        if column_name not in existing:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column_def}')


STATUS_TABLES = {
    "Approved": "timecards_approved",
    "Pending": "timecards_pending",
    "Rejected": "timecards_rejected",
}

# The business splits its work into two project types, told apart by the code
# the project name starts with: "FB..." (FBGIU ...) is Food & Beverage, "HL..."
# (HLGIU ...) is Hospitality. Each gets its own table (see
# _rebuild_project_type_tables), holding the approved AND pending entries of
# that type -- the two statuses that are still live work. Rejected entries
# belong to neither: they were turned down, so they aren't part of either
# division's book of work. A project named after neither code (e.g. "Global
# Customer Support Platform Upgrade") belongs to no division and lands in
# neither table.
PROJECT_TYPE_TABLES = {
    "beverage": "timecards_food_beverage",
    "hospitality": "timecards_hospitality",
}
PROJECT_TYPE_PREFIXES = {
    "beverage": "FB",
    "hospitality": "HL",
}
PROJECT_TYPE_LABELS = {
    "beverage": "Food & Beverage",
    "hospitality": "Hospitality",
}
# Which status tables feed the project-type tables, and the value each
# contributes to their "status" column.
PROJECT_TYPE_SOURCE_STATUSES = ("Approved", "Pending")


def _project_type_clause(project_type):
    """
    (sql_fragment, params) restricting a query to one project type, by the
    code "Project Name" starts with -- case-insensitively, and tolerant of a
    stray leading space. Returns ("", []) for None or an unknown type, i.e.
    "all project types", so callers can splice it in unconditionally.
    """
    prefix = PROJECT_TYPE_PREFIXES.get(project_type)
    if prefix is None:
        return "", []
    return (
        f'UPPER(SUBSTR(TRIM("Project Name"), 1, {len(prefix)})) = ?',
        [prefix.upper()],
    )


def get_status_project_counts(start_date=None, end_date=None, project_type=None):
    """
    Return row counts for approved, pending, and rejected records. When
    start_date/end_date are given, only rows whose "received" timestamp
    falls within [start_date, end_date] are counted (see date_utils for
    building these from a UI period choice) -- mirrors get_status_rows so
    the Dashboard's stat cards and table always agree on the same window.

    project_type ("beverage"/"hospitality", or None for all) narrows the
    count to one division, by the same project-name rule the project-type
    tables are built on.
    """
    where, params = _project_type_clause(project_type)
    where_sql = f" WHERE {where}" if where else ""

    conn = sqlite3.connect(DB_PATH)
    try:
        counts = {}
        for label, table_name in STATUS_TABLES.items():
            if conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone() is None:
                counts[label.lower()] = 0
                continue

            if start_date is None and end_date is None:
                row = conn.execute(
                    f'SELECT COUNT(*) FROM "{table_name}"{where_sql}', params
                ).fetchone()
                counts[label.lower()] = row[0] or 0
            else:
                received_values = conn.execute(
                    f'SELECT received FROM "{table_name}"{where_sql}', params
                ).fetchall()
                counts[label.lower()] = sum(
                    1 for (received,) in received_values if _received_in_range(received, start_date, end_date)
                )

        return {
            "approve": counts.get("approved", 0),
            "pending": counts.get("pending", 0),
            "reject": counts.get("rejected", 0),
        }
    finally:
        conn.close()


_STATUS_LABELS = {"approve": "Approved", "pending": "Pending", "reject": "Rejected"}


def get_status_columns(status_key="approve"):
    """
    Returns the column names of a status table, in schema order, without
    reading any rows. Lets the Dashboard show the full set of headings on
    an empty table (before the first scan), where get_status_rows has no
    row to take keys from.
    """
    table_name = STATUS_TABLES.get(_STATUS_LABELS.get(status_key, "Approved"))
    if table_name is None:
        return []

    conn = sqlite3.connect(DB_PATH)
    try:
        return [row[1] for row in conn.execute(f'PRAGMA table_info("{table_name}")')]
    finally:
        conn.close()


def get_status_rows(status_key, start_date=None, end_date=None, project_type=None):
    """
    Return rows for the selected status table as dictionaries. When
    start_date/end_date are given, only rows whose "received" timestamp
    falls within [start_date, end_date] are returned.

    project_type ("beverage"/"hospitality", or None for all) narrows the
    rows to one division. For the Approved and Pending statuses that gives
    exactly what sits in that division's project-type table; Rejected has
    no such table (rejected work belongs to neither division's book), but
    the same project-name rule still applies here so the Dashboard can
    filter all three statuses consistently.
    """
    table_name = STATUS_TABLES.get(_STATUS_LABELS.get(status_key, "Approved"))
    if table_name is None:
        return []

    where, params = _project_type_clause(project_type)
    where_sql = f" WHERE {where}" if where else ""

    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute(
            f'SELECT * FROM "{table_name}"{where_sql} ORDER BY "Date" ASC, subject ASC',
            params,
        )
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        if start_date is not None or end_date is not None:
            rows = [row for row in rows if _received_in_range(row.get("received"), start_date, end_date)]
        return rows
    finally:
        conn.close()


def get_project_type_rows(project_type, start_date=None, end_date=None):
    """
    Rows straight out of one project-type table (timecards_food_beverage /
    timecards_hospitality) -- approved and pending entries of that division
    together, each carrying a "status" column saying which it is. Unlike
    get_status_rows this reads the derived table itself, so it's the view
    to use when the division, not the status, is what's being reported on.
    """
    table_name = PROJECT_TYPE_TABLES.get(project_type)
    if table_name is None:
        return []

    conn = sqlite3.connect(DB_PATH)
    try:
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        ).fetchone() is None:
            return []

        cursor = conn.execute(
            f'SELECT * FROM "{table_name}" ORDER BY "Date" ASC, subject ASC'
        )
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        if start_date is not None or end_date is not None:
            rows = [row for row in rows if _received_in_range(row.get("received"), start_date, end_date)]
        return rows
    finally:
        conn.close()


# Columns search_records is allowed to match against. Also the vocabulary the
# Records page offers in its field picker -- keeping the list here means the
# UI and the WHERE clause can't drift apart (and, since field names get
# interpolated into SQL, acts as the whitelist that keeps arbitrary strings
# out of the query).
SEARCHABLE_FIELDS = [
    "subject", "sender", "Project Number", "Project Name", "Task Name",
    "name", "person_number",
]


def search_records(query="", fields=None):
    """
    Searches across all three status tables and returns full rows (every
    column, SELECT *). An empty query returns everything. Each result dict
    is tagged with "status" (Approved/Pending/Rejected) so callers can
    show where a match currently stands.

    fields limits which columns the query is matched against; it must be
    a subset of SEARCHABLE_FIELDS (anything else is ignored). None or an
    empty list means all of them -- the pre-field-picker behavior.
    """
    if fields:
        fields = [f for f in fields if f in SEARCHABLE_FIELDS]
    if not fields:
        fields = SEARCHABLE_FIELDS

    conn = sqlite3.connect(DB_PATH)
    try:
        results = []
        term = query.strip()
        like = f"%{term}%"
        for status_label, table_name in STATUS_TABLES.items():
            if conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
            ).fetchone() is None:
                continue

            if term:
                where = " OR ".join(f'"{field}" LIKE ?' for field in fields)
                cursor = conn.execute(
                    f'SELECT * FROM "{table_name}" WHERE {where}',
                    [like] * len(fields),
                )
            else:
                cursor = conn.execute(f'SELECT * FROM "{table_name}"')

            columns = [desc[0] for desc in cursor.description]
            for row in cursor.fetchall():
                record = dict(zip(columns, row))
                record["status"] = status_label
                results.append(record)

        results.sort(key=lambda r: r.get("Date") or "", reverse=True)
        return results
    finally:
        conn.close()


def update_status_record_field(status_key, record_id, column, value):
    """
    Writes one cell edit from the Dashboard grid back to the record's row
    (identified by id) in the status table status_key maps to. The column
    must actually exist on the table -- column names can't be bound as SQL
    parameters, so this check is what keeps the interpolation safe -- and
    "id" itself is refused. Returns True when a row was updated; False for
    an unknown column/table, a missing id, or an edit the table's UNIQUE
    constraint rejects (e.g. making a row a duplicate of another).
    """
    table_name = STATUS_TABLES.get(_STATUS_LABELS.get(status_key, "Approved"))
    if table_name is None or column in ("id", "received_month"):
        return False

    conn = sqlite3.connect(DB_PATH)
    try:
        valid_columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table_name}")')}
        if column not in valid_columns:
            return False
        try:
            if column == "received":
                # received_month is derived from received and is half of what
                # makes a row unique -- editing one without the other would
                # leave the row keyed under a month it isn't in.
                cursor = conn.execute(
                    f'UPDATE "{table_name}" SET received = ?, received_month = ? WHERE id = ?',
                    (value, _received_month(value), record_id),
                )
            else:
                cursor = conn.execute(
                    f'UPDATE "{table_name}" SET "{column}" = ? WHERE id = ?',
                    (value, record_id),
                )
        except sqlite3.IntegrityError:
            return False
        if cursor.rowcount > 0:
            # The division tables are copies of these rows -- a cell edit here
            # (a rate typed in, or a "Project Name" corrected, which can move
            # an entry from one division to the other or out of both) has to be
            # carried into them now. Waiting for the next scan would leave them
            # showing the pre-edit value in the meantime.
            _rebuild_project_type_tables(conn)
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def _parse_received(value):
    """Best-effort parse of the "received" column back into a naive
    datetime, tolerant of the various formats it may have been stored in
    (see _to_row -- it comes straight from Outlook's item.ReceivedTime)."""
    if not value:
        return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _received_in_range(received_value, start_date, end_date):
    """True if the stored "received" text falls within [start_date,
    end_date] (either bound may be None for unbounded). A row whose
    "received" can't be parsed is excluded once a range is in effect --
    there's no timestamp to judge it by."""
    parsed = _parse_received(received_value)
    if parsed is None:
        return False
    if start_date is not None and parsed < start_date:
        return False
    if end_date is not None and parsed > end_date:
        return False
    return True


def get_stale_status_counts(min_age_hours):
    """
    Counts Pending/Rejected rows whose "received" timestamp is at least
    min_age_hours in the past. Backs the Settings-configurable "N
    requests pending/rejected for X time" notification banner.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now()
        counts = {"pending": 0, "rejected": 0}
        for status_label in ("Pending", "Rejected"):
            table_name = STATUS_TABLES[status_label]
            if conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
            ).fetchone() is None:
                continue
            for (received,) in conn.execute(f'SELECT received FROM "{table_name}"'):
                parsed = _parse_received(received)
                if parsed is None:
                    continue
                age_hours = (now - parsed).total_seconds() / 3600
                if age_hours >= min_age_hours:
                    counts[status_label.lower()] += 1
        counts["total"] = counts["pending"] + counts["rejected"]
        return counts
    finally:
        conn.close()


def get_stale_records(min_age_hours):
    """
    Like get_stale_status_counts, but returns the actual Pending/Rejected
    rows (not just counts), each tagged with "status" and "age_hours".
    Backs the Late tab's "pending/rejected for X days/weeks" list.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        now = datetime.now()
        results = []
        for status_label in ("Pending", "Rejected"):
            table_name = STATUS_TABLES[status_label]
            if conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
            ).fetchone() is None:
                continue
            cursor = conn.execute(f"""
                SELECT subject, sender, "Project Number", "Project Name", "Task Name",
                       "Date", "Qty", received
                FROM "{table_name}"
            """)
            columns = [desc[0] for desc in cursor.description]
            for row in cursor.fetchall():
                record = dict(zip(columns, row))
                parsed = _parse_received(record.get("received"))
                if parsed is None:
                    continue
                age_hours = (now - parsed).total_seconds() / 3600
                if age_hours >= min_age_hours:
                    record["status"] = status_label
                    record["age_hours"] = age_hours
                    results.append(record)

        results.sort(key=lambda r: r["age_hours"], reverse=True)
        return results
    finally:
        conn.close()


def _create_timecards_table(conn, table_name):
    # What makes two entries the SAME entry: the same work day, on the same
    # project and task, from the same person, received in the same month.
    # received_month is in the key on purpose -- the same timecard received
    # in a different month is a separate entry, not a duplicate to overwrite.
    # It's a stored column rather than an expression because SQLite can't put
    # substr(received, 1, 7) inside a table-level UNIQUE constraint.
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS "{table_name}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT,
            "Date" TEXT,
            labor_type TEXT,
            time_type TEXT,
            "Qty" TEXT,
            "Project Number" TEXT,
            "Project Name" TEXT,
            "Task Name" TEXT,
            name TEXT,
            period TEXT,
            person_number TEXT,
            subject TEXT,
            sender TEXT,
            received TEXT,
            received_month TEXT,
            is_exported INTEGER DEFAULT 0,
            rate REAL DEFAULT 0,
            ----UNIQUE(day, "Project Number", "Task Name", sender, received_month)
            ----UNIQUE(day, "Project Number","Project Name",person_number ,"Task Name", received_month)
            UNIQUE(day, "Project Number", "Task Name", person_number, received_month)

        )
    """)


def _needs_received_month_rebuild(conn, table_name):
    """
    True when table_name exists but predates received_month. Such a table was
    created before the column joined the row key, and can't be patched in place
    (see _rebuild_status_table_with_received_month). A table that doesn't exist
    yet returns False -- _create_timecards_table builds it correctly from the
    start.
    """
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table_name}")')}
    return bool(columns) and "received_month" not in columns


def _rebuild_status_table_with_received_month(conn, table_name):
    """
    Brings a status table created before received_month existed up to the
    current schema. received_month is half of the row key and part of the
    table's UNIQUE constraint, so ALTER TABLE ADD COLUMN can't introduce it:
    that can't extend a UNIQUE constraint, and _upsert_timecard's ON CONFLICT
    needs the constraint to match. The table is rebuilt instead -- renamed
    aside, a fresh one created, existing rows copied over with received_month
    derived from each row's received timestamp, then the old table dropped.

    Row ids are carried across so invoice_lines.timecard_id links back into the
    approved table still resolve. When two old rows collapse onto the new
    (day, project, task, person, month) key, the one with the latest received
    wins -- the same "newest state is current" rule _save_row enforces.
    """
    tmp_name = f"{table_name}__pre_received_month"
    conn.execute(f'DROP TABLE IF EXISTS "{tmp_name}"')
    conn.execute(f'ALTER TABLE "{table_name}" RENAME TO "{tmp_name}"')
    _create_timecards_table(conn, table_name)

    old_columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{tmp_name}")')}
    new_columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table_name}")')]

    # Copy every column the new table has that the old one still carries, apart
    # from received_month, which is derived below rather than copied.
    carried = [c for c in new_columns if c in old_columns and c != "received_month"]
    select_exprs = []
    for column in carried:
        # person_number is part of the key and must never be NULL (see _to_row);
        # an old row that predates the column needs a concrete '' instead.
        if column == "person_number":
            select_exprs.append("COALESCE(\"person_number\", '')")
        else:
            select_exprs.append(f'"{column}"')

    has_received = "received" in old_columns
    received_select = 'substr("received", 1, 7)' if has_received else "NULL"
    order_sql = 'ORDER BY "received"' if has_received else ""
    carried_sql = ", ".join(f'"{c}"' for c in carried)
    select_sql = ", ".join(select_exprs)

    conn.execute(f"""
        INSERT OR REPLACE INTO "{table_name}" ({carried_sql}, received_month)
        SELECT {select_sql}, {received_select}
        FROM "{tmp_name}"
        {order_sql}
    """)
    conn.execute(f'DROP TABLE IF EXISTS "{tmp_name}"')


def _create_project_type_table(conn, table_name):
    # A division's book of live work: the approved and pending entries whose
    # project name starts with that division's letter, copied out of the two
    # status tables. status says which of the two an entry came from, and
    # source_id is its id in that table (the pair is unique -- an entry exists
    # in exactly one status table at a time, see _save_row).
    #
    # It's a copy, not a view, so a division can be queried, exported and
    # reported on on its own. Nothing writes to it directly: it is emptied and
    # rebuilt from the status tables on every save (_rebuild_project_type_tables),
    # which is what keeps it from drifting out of step with them.
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS "{table_name}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            status TEXT,
            day TEXT,
            "Date" TEXT,
            labor_type TEXT,
            time_type TEXT,
            "Qty" TEXT,
            "Project Number" TEXT,
            "Project Name" TEXT,
            "Task Name" TEXT,
            name TEXT,
            period TEXT,
            person_number TEXT,
            subject TEXT,
            sender TEXT,
            received TEXT,
            received_month TEXT,
            is_exported INTEGER DEFAULT 0,
            rate REAL DEFAULT 0,
            UNIQUE(status, source_id)
        )
    """)


# The columns a project-type row carries over from its status table, in the
# order both the INSERT and the SELECT below use.
_PROJECT_TYPE_COPIED_COLUMNS = [
    "day", '"Date"', "labor_type", "time_type", '"Qty"', '"Project Number"',
    '"Project Name"', '"Task Name"', "name", "period", "person_number",
    "subject", "sender", "received", "received_month", "is_exported", "rate",
]


def _rebuild_project_type_tables(conn):
    """
    Recomputes both project-type tables from scratch out of the Approved and
    Pending status tables, routing each entry by the first letter of its
    project name (B -> Food & Beverage, H -> Hospitality).

    Rebuilt rather than appended to: an entry that changes status, gets its
    project name corrected, or is dropped from its status table entirely
    would otherwise leave a stale copy behind in the division tables. An
    entry whose project name starts with neither letter belongs to no
    division and simply appears in neither table.
    """
    for project_type, table_name in PROJECT_TYPE_TABLES.items():
        # Same rule the Dashboard's project-type filter runs on, so the tables
        # and the filtered views of the status tables can't disagree.
        where, where_params = _project_type_clause(project_type)
        conn.execute(f'DELETE FROM "{table_name}"')
        for status_label in PROJECT_TYPE_SOURCE_STATUSES:
            source_table = STATUS_TABLES[status_label]
            # Only copy the columns the source actually has. A database created
            # before a column was added to the status tables (received_month is
            # the live case -- _ensure_columns never backfilled it) still has
            # rows worth routing into a division; the missing column is simply
            # left NULL here rather than failing the whole rebuild.
            source_columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{source_table}")')}
            copied = [
                column for column in _PROJECT_TYPE_COPIED_COLUMNS
                if column.strip('"') in source_columns
            ]
            if not copied:
                continue
            columns_sql = ", ".join(copied)
            conn.execute(f"""
                INSERT INTO "{table_name}" (source_id, status, {columns_sql})
                SELECT id, ?, {columns_sql}
                FROM "{source_table}"
                WHERE {where}
            """, [status_label, *where_params])


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    # timecards used to hold approved entries only (that was the only
    # status ever fetched). It's renamed aside into timecards_approved so
    # existing data isn't lost, now that pending/rejected get their own
    # tables too.
    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "timecards" in existing_tables and "timecards_approved" not in existing_tables:
        conn.execute("ALTER TABLE timecards RENAME TO timecards_approved")

    for table_name in STATUS_TABLES.values():
        # A table created before received_month existed can't be patched in
        # place (the column is part of the UNIQUE key) -- rebuild it first so
        # the CREATE/ensure below act on a table that already has the column.
        if _needs_received_month_rebuild(conn, table_name):
            _rebuild_status_table_with_received_month(conn, table_name)
        _create_timecards_table(conn, table_name)
        # ADD COLUMN with a DEFAULT backfills existing rows with it, so
        # records created before these columns existed start as
        # not-exported / rate 0 rather than NULL. Neither column is in
        # _upsert_timecard's ON CONFLICT update list, so a re-scan never
        # resets an is_exported flag or a manually entered rate.
        _ensure_columns(conn, table_name, [
            "name TEXT", "period TEXT", "person_number TEXT",
            "is_exported INTEGER DEFAULT 0", "rate REAL DEFAULT 0",
        ])

    for table_name in PROJECT_TYPE_TABLES.values():
        _create_project_type_table(conn, table_name)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS timecards_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT,
            "Period" TEXT,
            "Date" TEXT,
            labor_type TEXT,
            time_type TEXT,
            "Qty" REAL,
            "Project Number" TEXT,
            "Project Name" TEXT,
            "Task Name" TEXT,
            "Name" TEXT,
            "Person Number" TEXT,
            subject TEXT,
            sender TEXT,
            received TEXT
        )
    """)
    _ensure_columns(conn, "timecards_summary", ['"Name" TEXT', '"Person Number" TEXT'])

    # invoice_lines used to be one row per (Project Number, Task Name,
    # Period). It's now one row per raw timecard entry (day-level),
    # identified internally via timecard_id -- an older-shape table (no
    # timecard_id column) is renamed aside rather than dropped, so any
    # manually-entered data isn't lost, then rebuilt fresh below.
    existing_invoice_columns = {row[1] for row in conn.execute('PRAGMA table_info("invoice_lines")')}
    if existing_invoice_columns and "timecard_id" not in existing_invoice_columns:
        conn.execute("ALTER TABLE invoice_lines RENAME TO invoice_lines_period_level_backup")

    # Invoicing worksheet: one row per raw timecard entry (approved only).
    # Only the columns we can derive automatically are ever populated by
    # sync code (see _sync_invoice_lines below) -- everything else here is
    # filled in manually later and is never touched by a re-sync.
    # timecard_id links back to timecards_approved.id purely so
    # re-syncing can tell which entries already have a row here, without
    # touching any manual edits.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invoice_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timecard_id INTEGER UNIQUE,
            "Date" TEXT,
            "Invoice Number" TEXT,
            "Project Name" TEXT,
            "Period" TEXT,
            "Task Name" TEXT,
            "Project Mgr" TEXT,
            "PO" TEXT,
            "SOW" TEXT,
            "Line" TEXT,
            "Type" TEXT,
            "Project Number" TEXT,
            "Consultant" TEXT,
            "Qty" REAL,
            "Sales Price" REAL,
            "Total Amount" REAL GENERATED ALWAYS AS ("Qty" * "Sales Price")
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS export_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            date TEXT
        )
    """)

    # A single-row-per-key store for small pieces of app state that must
    # survive between runs. Currently holds one key, 'last_export_date':
    # the received-"to" date of the most recent range export, overwritten on
    # every export so the History page can offer a "From: last export" range.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Backfill: the project-type tables are otherwise only written on a save,
    # so on a database that already holds approved/pending records from before
    # they existed they'd sit empty until the next scan. Rebuilding here fills
    # them from what's already stored; on an up-to-date database it recomputes
    # the same rows, which is harmless.
    _rebuild_project_type_tables(conn)

    conn.commit()
    conn.close()


# Ensure the database exists and all expected tables are present as soon as
# the storage layer is imported.
init_db()


def _record_export(conn, name: str):
    conn.execute(
        "INSERT INTO export_history (name, date) VALUES (?, ?)",
        (name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )


def get_export_history():
    """Returns (name, date) rows for every export ever done, newest first."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT name, date FROM export_history ORDER BY date DESC, id DESC"
    ).fetchall()
    conn.close()
    return rows


def _set_last_export_date(conn, end_date: str):
    """
    Overwrites the stored "last export received-to date" with end_date
    (a 'YYYY-MM-DD' string). Single row, keyed 'last_export_date', so each
    export replaces the previous value rather than accumulating history --
    the export_history table already keeps the full log. Takes an open conn
    so it can ride inside the export's own transaction/commit.
    """
    conn.execute(
        "INSERT INTO app_state (key, value) VALUES ('last_export_date', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (end_date,),
    )


def get_last_export_date():
    """
    The received-"to" date ('YYYY-MM-DD') of the most recent range export,
    or None if nothing has been exported yet. Backs the History page's
    "From: last export" button, which uses it as the range's start.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = 'last_export_date'"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _received_date_only(received: str) -> str:
    """
    Truncates the raw "received" timestamp (item.ReceivedTime, e.g.
    '2026-07-03 09:14:22') down to just the date part, 'YYYY-MM-DD'.
    Used as the "Date" column instead of the timecard's own work-day -
    the record should be dated by when the email arrived, not by which
    day of the timecard it happens to describe (matches how
    invoice_lines' "Date" already works in _sync_invoice_lines below).
    """
    if not received:
        return None
    return str(received)[:10]


def _parse_period(subject: str) -> str:
    """
    Extracts the timecard's full week range from the subject line, e.g.
    'Your Time Entries from 2026-06-27 to 2026-07-03 Were Approved'
    -> '2026-06-27 to 2026-07-03'
    """
    match = _PERIOD_PATTERN.search(subject or "")
    if match:
        return f"{match.group(1)} to {match.group(2)}"
    return None


def _join_distinct(values) -> str:
    seen = []
    for v in values:
        if v and v not in seen:
            seen.append(v)
    return ", ".join(seen)


def _received_month(received: str) -> str:
    """
    The 'YYYY-MM' the entry was received in, taken off the front of the raw
    "received" timestamp. Half of what makes a row unique -- see
    _create_timecards_table.
    """
    if not received:
        return None
    return str(received)[:7]


def _to_row(entry: dict) -> tuple:
    return (
        entry.get("day"),
        _received_date_only(entry.get("received")),
        entry.get("labor_type"),
        entry.get("time_type"),
        entry.get("hours"),
        entry.get("project_code"),
        entry.get("project_name"),
        entry.get("task"),
        entry.get("name"),
        entry.get("period"),
        # Never NULL: person_number is part of the UNIQUE key, and SQLite
        # counts every NULL as distinct from every other -- one missing
        # person number would quietly exempt that row from the whole
        # duplicate/transition rule (and "person_number = NULL" never
        # matches in _find_existing either). The extractor returns None
        # whenever a timecard uses the labelled "Person Number: 12345"
        # layout instead of the bare header its regex expects, so this is
        # a live case, not a theoretical one.
        entry.get("person_number") or "",
        entry.get("subject"),
        entry.get("sender"),
        entry.get("received"),
        _received_month(entry.get("received")),
    )


def _row_key(row: tuple) -> tuple:
    """
    (day, Project Number, Task Name, person_number, received_month) out of a
    _to_row tuple -- the status tables' UNIQUE key. It deliberately says
    nothing about status, so it identifies the same entry across all three
    of them.

    The entry is keyed by WHOSE timecard it is (person_number), not by who
    happened to send the email -- the same timecard forwarded by two people
    is one entry, not two.
    """
    return (row[0], row[5], row[7], row[10], row[14])


def _rebuild_summary(conn):
    """
    Recomputes timecards_summary from scratch out of the raw per-day
    timecards_approved table (summary/export stays approved-only). Merge
    key: sender + subject + Project Number + Task Name (i.e. same weekly
    timecard email, same project, same task).
    """
    cursor = conn.execute('SELECT * FROM timecards_approved')
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    groups = {}
    for row in rows:
        key = (row["sender"], row["subject"], row["Project Number"], row["Task Name"])
        groups.setdefault(key, []).append(row)

    merged_rows = []
    for (sender, subject, project_number, task_name), group in groups.items():
        days = sorted(
            {r["day"] for r in group if r["day"]},
            key=lambda d: _DAY_ORDER.index(d.split(",")[0].strip()) if d.split(",")[0].strip() in _DAY_ORDER else 99
        )
        dates = sorted(r["Date"] for r in group if r["Date"])

        total_qty = 0.0
        for r in group:
            try:
                total_qty += float(r["Qty"])
            except (TypeError, ValueError):
                pass

        period = _join_distinct(r["period"] for r in group) or _parse_period(subject)

        merged_rows.append((
            ", ".join(days),
            period,
            dates[0] if dates else None,
            _join_distinct(r["labor_type"] for r in group),
            _join_distinct(r["time_type"] for r in group),
            total_qty,
            project_number,
            _join_distinct(r["Project Name"] for r in group),
            task_name,
            _join_distinct(r["name"] for r in group),
            _join_distinct(r["person_number"] for r in group),
            subject,
            sender,
            _join_distinct(r["received"] for r in group),
        ))

    conn.execute("DELETE FROM timecards_summary")
    conn.executemany("""
        INSERT INTO timecards_summary
        (day, "Period", "Date", labor_type, time_type, "Qty", "Project Number", "Project Name", "Task Name", "Name", "Person Number", subject, sender, received)
        VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, merged_rows)


def _sync_invoice_lines(conn):
    """
    Adds one invoice_lines row per raw timecards_approved entry (invoicing
    is approved-only), for any entry not already linked (via timecard_id).
    Only ever INSERTs new rows (OR IGNORE on the timecard_id UNIQUE key)
    -- an existing row, including any Invoice Number/Sales Price/etc
    you've filled in by hand, is never touched or overwritten by a later
    sync. "Date" is the day the email itself was received (not the
    timecard's work-day).
    """
    rows = conn.execute("""
        SELECT id, "Project Name", period, "Task Name", "Project Number", "Qty", received
        FROM timecards_approved
    """).fetchall()

    prepared = [
        (timecard_id, received[:10] if received else None, project_name, period, task_name, project_number, qty)
        for timecard_id, project_name, period, task_name, project_number, qty, received in rows
    ]

    conn.executemany("""
        INSERT OR IGNORE INTO invoice_lines
        (timecard_id, "Date", "Project Name", "Period", "Task Name", "Project Number", "Qty")
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, prepared)


def _prune_invoice_lines(conn):
    """
    Drops invoice_lines whose timecard is no longer in timecards_approved.
    An entry whose approval was revoked (it moved to Pending/Rejected -- see
    _save_row) isn't billable, and leaving its line behind would both invoice
    it anyway and double-bill it if it were approved again later: the
    re-approval inserts a fresh timecards_approved row, so _sync_invoice_lines
    would add a SECOND line beside the orphan.
    """
    conn.execute("""
        DELETE FROM invoice_lines
        WHERE timecard_id IS NOT NULL
          AND timecard_id NOT IN (SELECT id FROM timecards_approved)
    """)


def _dedupe_latest_across_statuses(grouped):
    """
    Collapses entries sharing the same _row_key down to the single
    latest-received one, ACROSS the status tables they'd otherwise be
    routed to. Takes and returns a {table_name: [row, ...]} mapping.

    Within one table, a batch can contain the same logical entry twice (a
    resend alongside the real one) and the last one written would win --
    but emails are scanned newest-received-first (see filter_service), so
    an OLDER duplicate lands later in the batch and would overwrite the
    newer one, exactly backwards. Across tables, one scan can pick up both
    a Pending email and the Approval that superseded it; only the later of
    the two is the entry's current state, and letting both through would
    leave a stale row in the other table.
    """
    best = {}
    for table_name, rows in grouped.items():
        for row in rows:
            key = _row_key(row)
            existing = best.get(key)
            if existing is None:
                best[key] = (table_name, row)
                continue
            new_parsed = _parse_received(row[13])
            existing_parsed = _parse_received(existing[1][13])
            if new_parsed and (existing_parsed is None or new_parsed > existing_parsed):
                best[key] = (table_name, row)

    resolved = {}
    for table_name, row in best.values():
        resolved.setdefault(table_name, []).append(row)
    return resolved


def _find_existing(conn, key):
    """
    Every stored row matching key, in whichever status table it sits.
    Returns (table_name, id, received, rate, is_exported) tuples.
    """
    found = []
    for table_name in STATUS_TABLES.values():
        for row in conn.execute(f"""
            SELECT id, received, rate, is_exported FROM "{table_name}"
            WHERE day = ? AND "Project Number" = ? AND "Task Name" = ?
              AND person_number = ? AND received_month = ?
        """, key):
            found.append((table_name, *row))
    return found


def _upsert_timecard(conn, table_name, row):
    # The ON CONFLICT column list must name exactly the columns of the
    # table's UNIQUE constraint (see _create_timecards_table) -- SQLite
    # raises "ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE
    # constraint" otherwise. The key columns are not in the DO UPDATE list
    # (they're what was matched on); sender is, since it's no longer part of
    # the key and a later forward of the same timecard should refresh it.
    conn.execute(f"""
        INSERT INTO "{table_name}"
        (day, "Date", labor_type, time_type, "Qty", "Project Number", "Project Name", "Task Name", name, period, person_number, subject, sender, received, received_month)
        VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(day, "Project Number", "Task Name", person_number, received_month) DO UPDATE SET
            "Date" = excluded."Date",
            labor_type = excluded.labor_type,
            time_type = excluded.time_type,
            "Qty" = excluded."Qty",
            "Project Name" = excluded."Project Name",
            name = excluded.name,
            period = excluded.period,
            subject = excluded.subject,
            sender = excluded.sender,
            received = excluded.received
    """, row)


def _save_row(conn, table_name, row):
    """
    Writes one entry into its status table, holding the rule that an entry
    exists exactly once across the three of them: same day, project, task,
    person_number and received-MONTH is the same entry, and the latest
    "received" is its current state.

    So an entry that changes status within a month -- Pending or Rejected
    -> Approved, an approval later revoked, any direction -- is MOVED: the
    row is deleted from the table it used to sit in and written to the new
    one. An entry received in a DIFFERENT month is a different entry and
    simply coexists; the month is part of the key, so it never collides.

    A row already stored with a LATER "received" than the incoming one is
    left alone and the incoming row dropped -- re-reading an old Pending
    email must not undo the Approval that came after it.
    """
    key = _row_key(row)
    incoming = _parse_received(row[13])
    existing = _find_existing(conn, key)

    for _table, _id, stored_received, _rate, _is_exported in existing:
        stored = _parse_received(stored_received)
        if stored is not None and (incoming is None or stored > incoming):
            return

    # A rate is typed in by hand and is_exported records that the entry went
    # out in a file; neither is a property of the status it happened to be
    # sitting under, so they ride along when the entry is moved.
    carried_rate = 0.0
    carried_is_exported = 0
    for other_table, row_id, _received, rate, is_exported in existing:
        if other_table == table_name:
            continue  # same table: the upsert below updates it in place
        carried_rate = carried_rate or (rate or 0)
        carried_is_exported = max(carried_is_exported, is_exported or 0)
        conn.execute(f'DELETE FROM "{other_table}" WHERE id = ?', (row_id,))

    _upsert_timecard(conn, table_name, row)

    if carried_rate or carried_is_exported:
        conn.execute(f"""
            UPDATE "{table_name}" SET
                rate = CASE WHEN COALESCE(rate, 0) = 0 THEN ? ELSE rate END,
                is_exported = CASE WHEN COALESCE(is_exported, 0) = 0 THEN ? ELSE is_exported END
            WHERE day = ? AND "Project Number" = ? AND "Task Name" = ?
              AND person_number = ? AND received_month = ?
        """, (carried_rate, carried_is_exported, *key))


def _group_by_status_table(entries):
    """
    Buckets entries by their target table (timecards_approved/_pending/
    _rejected) based on entry["status"]. Entries with a missing or
    unrecognized status are dropped -- there's no table to route them to.
    """
    grouped = {}
    skipped = 0
    for entry in entries:
        table_name = STATUS_TABLES.get(entry.get("status"))
        if table_name is None:
            skipped += 1
            continue
        grouped.setdefault(table_name, []).append(_to_row(entry))
    if skipped:
        print(f"Skipped {skipped} entr{'y' if skipped == 1 else 'ies'} with unrecognized/missing status.")
    return grouped


def save_card(entry: dict):
    save_cards([entry])


def save_cards(entries: list):
    grouped = _dedupe_latest_across_statuses(_group_by_status_table(entries))
    if not grouped:
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        # Row by row rather than executemany: each one has to be weighed
        # against what's already stored under its key -- possibly in a
        # different status table -- before it can be written. See _save_row.
        for table_name, rows in grouped.items():
            for row in rows:
                _save_row(conn, table_name, row)
        _rebuild_summary(conn)
        _rebuild_project_type_tables(conn)
        _sync_invoice_lines(conn)
        # Comment out the next line to KEEP the invoice line of an entry whose
        # approval was later revoked (it moved to Pending/Rejected), instead of
        # deleting it. The trade either way:
        #   pruning (current)       -- the line goes, and any Invoice Number /
        #                              PO / Sales Price typed in by hand goes too.
        #   keeping (commented out) -- that manual data survives, but the entry
        #                              is invoiced despite no longer being
        #                              approved, and if it's ever approved again
        #                              it is billed TWICE: the re-approval is a
        #                              new timecards_approved row, so
        #                              _sync_invoice_lines adds a second line
        #                              next to the kept one.
        _prune_invoice_lines(conn)
        conn.commit()
    finally:
        # A raise anywhere above (a bad row, a schema mismatch) would otherwise
        # leak the connection and leave the database file locked for the rest
        # of the process -- the sync runs on a QThread, so the UI would keep
        # hitting a locked db long after the scan died.
        conn.close()


def export_to_csv(output_path="output.csv"):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute('SELECT * FROM timecards_summary ORDER BY "Date" ASC')
    columns = [desc[0] for desc in cursor.description]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(cursor.fetchall())

    conn.close()
    print(f"Exported to {output_path}")


def export_summary_csv_range(start_date: str, end_date: str, output_path: str, project_type=None) -> int:
    """
    Exports approved timecard rows whose "Date" falls within
    [start_date, end_date] (inclusive, both 'YYYY-MM-DD' strings) to a
    CSV at output_path. Backs the History page's "Export last month" /
    "Export date range" buttons. Records the export in export_history,
    same as the other export functions, so it shows up in that list too.
    Returns the number of rows written.

    project_type ("beverage"/"hospitality", or None for every project)
    narrows the export to one division, by the same project-name rule the
    project-type tables are built on -- so an export can cover just Food &
    Beverage or just Hospitality. Only the rows actually written are
    flagged is_exported, so exporting one division leaves the other's
    entries still marked un-exported.

    Reads timecards_approved rather than timecards_summary so the export
    keeps one row per day, exactly like the Dashboard's records table
    (see get_status_rows) -- the summary table merges a whole timecard
    email into a single row with the days joined and Qty summed. The
    leading columns are the Dashboard's, in its order; the rest follow so
    no field is dropped.

    Every exported record gets its is_exported flag set to 1 (a no-op for
    rows already flagged from an earlier export). The id is fetched only
    for that flagging and is not written to the CSV; is_exported itself is
    left out of the file too -- it's bookkeeping, not timecard data.
    """
    type_where, type_params = _project_type_clause(project_type)
    type_sql = f" AND {type_where}" if type_where else ""

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        'SELECT id, subject, "Project Number", "Project Name", "Task Name", "Date", "Qty", rate, '
        'day, period, labor_type, time_type, name, person_number, sender, received '
        f'FROM timecards_approved WHERE "Date" >= ? AND "Date" <= ?{type_sql} '
        'ORDER BY "Date" ASC, subject ASC',
        [start_date, end_date, *type_params],
    )
    columns = [desc[0] for desc in cursor.description][1:]  # drop id
    rows = cursor.fetchall()

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(row[1:] for row in rows)

    conn.executemany(
        "UPDATE timecards_approved SET is_exported = 1 WHERE id = ? AND is_exported != 1",
        [(row[0],) for row in rows],
    )
    _record_export(conn, os.path.basename(output_path))
    # Remember how far this export reached, so the next one can start where
    # this one left off. Overwritten every export (see _set_last_export_date).
    _set_last_export_date(conn, end_date)
    conn.commit()
    conn.close()
    print(f"Exported {len(rows)} row(s) ({start_date} to {end_date}) to {output_path}")
    return len(rows)


def export_act_invoice_overview_range(start_date: str, end_date: str, output_path: str, project_type=None) -> int:
    """
    Exports approved timecard rows whose "Date" falls within
    [start_date, end_date] straight into the ACT "Invoice Overview per
    Period" template layout (.xlsx) -- same query/filters/bookkeeping as
    export_summary_csv_range, but the file this produces opens as the
    proper template instead of a flat CSV.

    Each timecard entry becomes a LABOR row immediately followed by an
    Expense row, mirroring the template's existing pattern:
      LABOR row   -> Date, PO (fixed), Line=1, Type=LABOR, Project Number,
                     Project Name, Period, Task Name, Qty, Sales Price
                     (=rate), Total Amount (=Qty*Sales Price formula).
                     Invoice No / Project Mgr / SOW / Consultant are left
                     blank -- not tracked by this system.
      Expense row -> nothing but Line=2, Type=Expense, Sales Price=0,
                     and the Total Amount formula (matches the template's
                     own blank Expense rows exactly).

    Returns the number of source timecard rows written (i.e. half the
    number of spreadsheet rows, since each becomes a LABOR+Expense pair).
    """
    type_where, type_params = _project_type_clause(project_type)
    type_sql = f" AND {type_where}" if type_where else ""

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        'SELECT id, "Project Number", "Project Name", "Task Name", "Qty", rate, day, period '
        f'FROM timecards_approved WHERE "Date" >= ? AND "Date" <= ?{type_sql} '
        'ORDER BY "Date" ASC, subject ASC',
        [start_date, end_date, *type_params],
    )
    rows = cursor.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Billed Invoice Details"

    ws.merge_cells("B2:D2")
    ws["B2"] = _ACT_TITLE

    header_row = 4
    for col_offset, header in enumerate(_ACT_HEADERS):
        cell = ws.cell(row=header_row, column=2 + col_offset, value=header)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL

    r = header_row + 1
    for _id, project_number, project_name, task_name, qty, rate, day, period in rows:
        qty_val = float(qty) if qty not in (None, "") else None
        rate_val = float(rate) if rate not in (None, "") else 0.0

        # LABOR row
        ws.cell(row=r, column=2, value=day)                 # Date (no year, as extracted)
        ws.cell(row=r, column=4, value=project_name)         # Project Name
        ws.cell(row=r, column=5, value=period)               # Period
        ws.cell(row=r, column=6, value=task_name)            # Task Name
        ws.cell(row=r, column=8, value=_ACT_PO)              # PO
        ws.cell(row=r, column=10, value=1)                   # Line
        ws.cell(row=r, column=11, value="LABOR")              # Type
        ws.cell(row=r, column=12, value=project_number)      # Project Number
        ws.cell(row=r, column=14, value=qty_val)              # Qty
        sp_cell = ws.cell(row=r, column=15, value=rate_val)   # Sales Price
        sp_cell.number_format = _AED_FORMAT
        total_cell = ws.cell(row=r, column=16, value=f"=N{r}*O{r}")  # Total Amount
        total_cell.number_format = _AED_FORMAT
        r += 1

        # Expense row -- nothing but Line=2, Type, Sales Price=0, Total formula
        ws.cell(row=r, column=10, value=2)
        ws.cell(row=r, column=11, value="Expense")
        sp_cell = ws.cell(row=r, column=15, value=0)
        sp_cell.number_format = _AED_FORMAT
        total_cell = ws.cell(row=r, column=16, value=f"=N{r}*O{r}")
        total_cell.number_format = _AED_FORMAT
        r += 1

    last_data_row = r - 1

    table = Table(displayName="Table4", ref=f"B{header_row}:P{last_data_row}")
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium23", showRowStripes=True,
    )
    ws.add_table(table)

    # Totals block, right after the data
    t = last_data_row + 1
    ws.cell(row=t, column=12, value="Total LABOR")
    ws.cell(row=t, column=14, value="=SUBTOTAL(109,Table4[Qty])")
    ws.cell(row=t, column=16, value='=SUMIF(Table4[Type],"LABOR",Table4[Total Amount])')

    ws.cell(row=t + 1, column=12, value="Total EXPENSE")
    ws.cell(row=t + 1, column=16, value='=SUMIF(Table4[Type],"Expense",Table4[Total Amount])')

    ws.cell(row=t + 2, column=12, value="Invoice Total ")
    ws.cell(row=t + 2, column=16, value=f"=P{t}+P{t + 1}")

    ws.cell(row=t + 3, column=13, value="VAT")
    ws.cell(row=t + 3, column=16, value=f"=P{t + 2}*0.05")

    ws.cell(row=t + 4, column=13, value="Total with VAT")
    ws.cell(row=t + 4, column=16, value=f"=P{t + 2}+P{t + 3}")

    for total_row in (t, t + 1, t + 2, t + 3, t + 4):
        cell = ws.cell(row=total_row, column=16)
        cell.number_format = _AED_FORMAT

    for col_letter, width in zip("BCDEFGHIJKLMNOP", [12, 11, 30, 16, 30, 13, 14, 8, 6, 8, 14, 14, 7, 13, 15]):
        ws.column_dimensions[col_letter].width = width

    wb.save(output_path)

    conn.executemany(
        "UPDATE timecards_approved SET is_exported = 1 WHERE id = ? AND is_exported != 1",
        [(row[0],) for row in rows],
    )
    _record_export(conn, os.path.basename(output_path))
    _set_last_export_date(conn, end_date)
    conn.commit()
    conn.close()
    print(f"Exported {len(rows)} row(s) ({start_date} to {end_date}) to {output_path}")
    return len(rows)


def export_invoice_lines_to_excel(output_path="invoice_lines.xlsx"):
    """
    Exports invoice_lines to a real formatted .xlsx (not .csv - a CSV is
    plain text and can't hold fonts/colors/borders at all). Header row is
    bold, larger, light-blue-filled; every cell in the table gets a thin
    border and black text.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("""
        SELECT "Date", "Invoice Number", "Project Name", "Period", "Task Name", "Project Mgr",
               "PO", "SOW", "Line", "Type", "Project Number", "Consultant", "Qty", "Sales Price",
               "Total Amount"
        FROM invoice_lines
        ORDER BY "Date" ASC
    """)
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice Lines"

    ws.append(columns)
    for cell in ws[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.border = _THIN_BORDER

    for i, row in enumerate(rows):
        ws.append(row)
        for cell in ws[ws.max_row]:
            cell.font = _BODY_FONT
            cell.border = _THIN_BORDER
        if i < len(rows) - 1:
            ws.append([])  # blank separator row between records - no styling

    for i, column_name in enumerate(columns, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(len(column_name) + 2, 12)

    wb.save(output_path)
    _record_export(conn, os.path.basename(output_path))
    conn.commit()
    conn.close()
    print(f"Exported to {output_path}")


if __name__ == "__main__":
    init_db()
    print("Database initialized at", DB_PATH)