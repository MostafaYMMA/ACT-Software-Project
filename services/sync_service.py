from filter_service import get_approved_cards, get_expense_reports
from extractor_service import extract, extract_expense
from storage_service import (
    init_db, save_cards, save_expenses,
    export_to_csv, export_invoice_lines_to_excel, export_expenses_to_csv,
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
        save_cards(all_entries)
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
