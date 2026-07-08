import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "services"))

from filter_service import get_approved_cards
from extractor_service import extract
from storage_service import init_db, save_cards, export_to_csv


def main():
    init_db()

    emails = get_approved_cards()
    print(f"\nApproved emails found: {len(emails)}")

    all_entries = []
    for email in emails:
        entries = extract(email)
        print(f"  - '{email.Subject}' -> {len(entries)} entries")
        all_entries.extend(entries)

    print(f"\nTotal entries extracted: {len(all_entries)}")

    if all_entries:
        save_cards(all_entries)
        print("Saved entries to database.")

        export_to_csv()
    else:
        print("Nothing to save.")


if __name__ == "__main__":
    main()
