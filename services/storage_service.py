import sqlite3
import csv
import os
import re
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side

DB_PATH = "data/cards.db"

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


def _create_timecards_table(conn, table_name):
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
            UNIQUE(day, "Project Number", "Task Name", sender)
        )
    """)


_OLD_UNIQUE_CLAUSE = 'UNIQUE(day, "Project Number", "Task Name", subject, sender)'


def _migrate_drop_subject_from_unique(conn, table_name):
    """
    subject used to be part of what makes a timecard row "the same" as
    another. It no longer is (see UNIQUE clause above) -- an existing
    table built under the old constraint is rebuilt here rather than
    left behind, so duplicates by the new (day, Project Number, Task
    Name, sender) key actually get collapsed instead of silently
    persisting side-by-side under the stale schema.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    if row is None or _OLD_UNIQUE_CLAUSE not in row[0]:
        return  # table doesn't exist yet, or already on the new schema

    backup_name = f"{table_name}_subject_key_backup"
    conn.execute(f'ALTER TABLE "{table_name}" RENAME TO "{backup_name}"')
    _create_timecards_table(conn, table_name)
    # INSERT OR REPLACE, ordered oldest id first, so when two old rows now
    # collide on the new key, the one from the more recent sync (higher id)
    # is the one that survives.
    conn.execute(f"""
        INSERT OR REPLACE INTO "{table_name}"
        (id, day, "Date", labor_type, time_type, "Qty", "Project Number", "Project Name",
         "Task Name", name, period, person_number, subject, sender, received)
        SELECT id, day, "Date", labor_type, time_type, "Qty", "Project Number", "Project Name",
               "Task Name", name, period, person_number, subject, sender, received
        FROM "{backup_name}"
        ORDER BY id ASC
    """)
    conn.execute(f'DROP TABLE "{backup_name}"')


def init_db():
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
        _migrate_drop_subject_from_unique(conn, table_name)
        _create_timecards_table(conn, table_name)
        _ensure_columns(conn, table_name, ["name TEXT", "period TEXT", "person_number TEXT"])

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

    conn.commit()
    conn.close()


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


def _parse_date(day_text: str) -> str:
    """
    Converts 'Monday, 29 Jun' -> '2026-06-29' (ISO format, sortable).
    Assumes the current year since the email doesn't include one.
    """
    try:
        cleaned = day_text.split(",")[1].strip()   # "29 Jun"
        parsed = datetime.strptime(cleaned, "%d %b")
        parsed = parsed.replace(year=datetime.now().year)
        return parsed.strftime("%Y-%m-%d")
    except Exception:
        return None


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


def _to_row(entry: dict) -> tuple:
    return (
        entry.get("day"),
        _parse_date(entry.get("day", "")),
        entry.get("labor_type"),
        entry.get("time_type"),
        entry.get("hours"),
        entry.get("project_code"),
        entry.get("project_name"),
        entry.get("task"),
        entry.get("name"),
        entry.get("period"),
        entry.get("person_number"),
        entry.get("subject"),
        entry.get("sender"),
        entry.get("received"),
    )


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


def _upsert_timecards(conn, table_name, rows):
    conn.executemany(f"""
        INSERT INTO "{table_name}"
        (day, "Date", labor_type, time_type, "Qty", "Project Number", "Project Name", "Task Name", name, period, person_number, subject, sender, received)
        VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(day, "Project Number", "Task Name", sender) DO UPDATE SET
            "Date" = excluded."Date",
            labor_type = excluded.labor_type,
            time_type = excluded.time_type,
            "Qty" = excluded."Qty",
            "Project Name" = excluded."Project Name",
            name = excluded.name,
            period = excluded.period,
            person_number = excluded.person_number,
            subject = excluded.subject,
            received = excluded.received
    """, rows)


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
    grouped = _group_by_status_table(entries)
    if not grouped:
        return

    conn = sqlite3.connect(DB_PATH)
    for table_name, rows in grouped.items():
        _upsert_timecards(conn, table_name, rows)
    _rebuild_summary(conn)
    _sync_invoice_lines(conn)
    conn.commit()
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
