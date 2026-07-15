from filter_service import get_approved_cards
from extractor_service import extract
from storage_service import init_db, save_cards, export_to_csv, export_invoice_lines_to_excel


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
