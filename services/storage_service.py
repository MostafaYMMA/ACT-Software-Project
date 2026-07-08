import sqlite3
import csv
import re
from datetime import datetime

DB_PATH = "data/cards.db"

_DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_PERIOD_PATTERN = re.compile(
    r"from\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE
)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timecards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT,
            "Date" TEXT,
            labor_type TEXT,
            time_type TEXT,
            "Qty" TEXT,
            "Project Number" TEXT,
            "Project Name" TEXT,
            "Task Name" TEXT,
            subject TEXT,
            sender TEXT,
            received TEXT,
            UNIQUE(day, "Project Number", "Task Name", subject, sender)
        )
    """)
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
            subject TEXT,
            sender TEXT,
            received TEXT
        )
    """)
    conn.commit()
    conn.close()


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
        entry.get("subject"),
        entry.get("sender"),
        entry.get("received"),
    )


def _rebuild_summary(conn):
    """
    Recomputes timecards_summary from scratch out of the raw per-day
    timecards table. Merge key: sender + subject + Project Number + Task Name
    (i.e. same weekly timecard email, same project, same task).
    """
    cursor = conn.execute('SELECT * FROM timecards')
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

        merged_rows.append((
            ", ".join(days),
            _parse_period(subject),
            dates[0] if dates else None,
            _join_distinct(r["labor_type"] for r in group),
            _join_distinct(r["time_type"] for r in group),
            total_qty,
            project_number,
            _join_distinct(r["Project Name"] for r in group),
            task_name,
            subject,
            sender,
            _join_distinct(r["received"] for r in group),
        ))

    conn.execute("DELETE FROM timecards_summary")
    conn.executemany("""
        INSERT INTO timecards_summary
        (day, "Period", "Date", labor_type, time_type, "Qty", "Project Number", "Project Name", "Task Name", subject, sender, received)
        VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, merged_rows)


def save_card(entry: dict):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO timecards
        (day, "Date", labor_type, time_type, "Qty", "Project Number", "Project Name", "Task Name", subject, sender, received)
        VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, _to_row(entry))
    _rebuild_summary(conn)
    conn.commit()
    conn.close()


def save_cards(entries: list):
    prepared = [_to_row(entry) for entry in entries]

    conn = sqlite3.connect(DB_PATH)
    conn.executemany("""
        INSERT OR REPLACE INTO timecards
        (day, "Date", labor_type, time_type, "Qty", "Project Number", "Project Name", "Task Name", subject, sender, received)
        VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, prepared)
    _rebuild_summary(conn)
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


if __name__ == "__main__":
    init_db()
    print("Database initialized at", DB_PATH)
