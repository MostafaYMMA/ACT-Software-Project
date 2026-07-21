import os
import tempfile
from datetime import datetime

from filter_service import get_approved_cards, get_expense_reports
from extractor_service import extract, extract_expense
from storage_service import (
    init_db, save_cards, save_expenses,
    export_to_csv, export_invoice_lines_to_excel, export_expenses_to_csv,
    build_outgoing_snapshot, export_snapshot_rows_to_excel,
)
from sharepoint_service import (
    require_sharepoint_folder, read_boundary_date, write_boundary,
    write_device_sheet, merge_device_sheets, print_workbook,
)


def sync_cards(progress_callback=None, start_date=None, end_date=None):
    """
    Pull approved timecard emails, extract entries, and persist them.

    start_date/end_date (optional): restrict the scan to emails whose
    Outlook "received on" time falls within [start_date, end_date]
    (see date_utils for building these from a UI period choice). When
    both are omitted, the 500 most recently received emails are scanned,
    with no date restriction.
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    init_db()

    has_range = start_date is not None or end_date is not None

    report("Checking inbox for approved timecards...")
    emails = get_approved_cards(
        limit=None if has_range else 500,
        start_date=start_date,
        end_date=end_date,
    )
    report(f"Approved emails found: {len(emails)}")

    all_entries = []
    for email in emails:
        entries = extract(email)
        print(f"  - '{email['subject']}' -> {len(entries)} entries")
        all_entries.extend(entries)

    report(f"Total entries extracted: {len(all_entries)}")

    if all_entries:
        report("Saving entries...")
        save_cards(all_entries)  # origin defaults to THIS device -- see storage_service.save_cards
        print("Saved entries to database.")
        export_to_csv()
        export_invoice_lines_to_excel()
    else:
        print("Nothing to save.")

    # --- Expense reports: a second, independent scan of the same window ---
    # (approved expense-report emails, filtered separately from timecards).
    report("Checking inbox for approved expense reports...")
    expense_emails = get_expense_reports(
        limit=None if has_range else 500,
        start_date=start_date,
        end_date=end_date,
        print_report=False,
    )
    report(f"Approved expense reports found: {len(expense_emails)}")

    expense_entries = []
    for email in expense_emails:
        entries = extract_expense(email)
        print(f"  - '{email['subject']}' -> {len(entries)} expense line(s)")
        expense_entries.extend(entries)

    report(f"Total expense line-items extracted: {len(expense_entries)}")

    if expense_entries:
        report("Saving expenses...")
        save_expenses(expense_entries)
        print("Saved expenses to database.")
        export_expenses_to_csv()
    else:
        print("No expenses to save.")


# ----------------------------------------------------------------------
# SharePoint file channel: Update / View Current / Finalize.
#
# The only cross-device sync channel in the app -- reads/writes files in
# a shared, locally-synced OneDrive/SharePoint folder (see
# services/sharepoint_service.py and SHAREPOINT_SYNC_SPEC.md). No mail
# involved and no shared mutable state with the rest of the app except
# the DB tables themselves (which this channel only ever reads, never
# writes).
#
# Each button re-runs the cheaper one beneath it first, so the user never
# acts on stale data. Do not flatten this nesting.
# ----------------------------------------------------------------------

def sharepoint_update(folder, progress_callback=None, project_type=None):
    """
    'Update' (SharePoint section): rebuilds THIS device's own
    current_<device_id>.xlsx from the DB and writes it into the
    SharePoint folder. Idempotent -- a full rebuild every time (RULE 1 in
    SHAREPOINT_SYNC_SPEC.md), never an append, so clicking it repeatedly
    produces the identical file.
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    require_sharepoint_folder(folder)

    report("Reading the shared boundary date...")
    boundary_date = read_boundary_date(folder)

    report("Rebuilding this device's current sheet from the database...")
    snapshot = build_outgoing_snapshot(project_type=project_type, since_date=boundary_date)
    path = write_device_sheet(folder, snapshot["rows"])

    report(f"Wrote {len(snapshot['rows'])} row(s) to {os.path.basename(path)}.")
    return {"file": path, "rows": len(snapshot["rows"])}


def sharepoint_view_current(folder, progress_callback=None, project_type=None):
    """
    'View Current': runs sharepoint_update first (so this device's own
    contribution is fresh), then reads and merges+dedups every device's
    current_*.xlsx in the folder. Display only -- writes nothing to disk
    or the DB.
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    update_result = sharepoint_update(folder, progress_callback=progress_callback, project_type=project_type)

    report("Merging every device's current sheet...")
    rows, sources = merge_device_sheets(folder)
    report(f"Merged {len(rows)} row(s) from {len(sources)} device sheet(s).")

    return {"rows": rows, "sources": sources, "update": update_result}


def sharepoint_finalize(folder, progress_callback=None, project_type=None, printer=None):
    """
    'Finalize' (SharePoint section): must be called only AFTER the user
    has confirmed in the UI. Runs sharepoint_view_current first, prints
    the merged sheet literally, and
    only on a successful print advances the ONE shared boundary.json
    (RULE 2) and resets this device's sheet to empty. A failed print
    raises before any of that happens, leaving the period open (spec sec 7).
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    view = sharepoint_view_current(folder, progress_callback=progress_callback, project_type=project_type)
    rows = view["rows"]

    report("Preparing the merged sheet for printing...")
    fd, tmp_path = tempfile.mkstemp(prefix="act_sharepoint_finalize_", suffix=".xlsx")
    os.close(fd)
    try:
        export_snapshot_rows_to_excel(rows, tmp_path)
        report("Printing...")
        print_workbook(tmp_path, printer=printer)  # raises on failure -- nothing below runs
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    report("Advancing the shared boundary...")
    new_boundary = datetime.now().strftime("%Y-%m-%d")
    write_boundary(folder, new_boundary)

    report("Resetting this device's current sheet...")
    sharepoint_update(folder, progress_callback=progress_callback, project_type=project_type)

    return {"printed": True, "boundary_date": new_boundary, "rows_printed": len(rows)}