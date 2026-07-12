from filter_service import get_approved_cards
from extractor_service import extract
from storage_service import init_db, save_cards, export_to_csv, export_invoice_lines_to_excel


def sync_cards(progress_callback=None):
    """Pull approved timecard emails, extract entries, and persist them."""

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    init_db()

    report("Checking inbox for approved timecards...")
    emails = get_approved_cards(limit=100)
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
