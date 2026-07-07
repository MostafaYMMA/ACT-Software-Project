import win32com.client
import re

approved_pattern = re.compile(r"\bapproved\b", re.IGNORECASE)
subject_pattern = re.compile(
    r"\btime(?:\s+|-)?card\b|^\s*FW:\s*FYI:?",
    re.IGNORECASE
)


def get_approved_cards(inbox=None, verbose=True):
    if inbox is None:
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        inbox = outlook.GetDefaultFolder(6)

    total = 0
    read = 0
    unread = 0
    approved_count = 0
    matching_emails = []

    for item in inbox.Items:
        try:
            if item.Class != 43:
                continue

            total += 1

            if item.UnRead:
                unread += 1
            else:
                read += 1

            subject = item.Subject or ""

            if approved_pattern.search(subject):
                approved_count += 1

                if subject_pattern.search(subject):
                    matching_emails.append(item)

        except Exception as e:
            print(f"Error: {e}")

    if verbose:
        print("\n========== RESULTS ==========")
        print(f"Total Emails                : {total:,}")
        print(f"Read Emails                 : {read:,}")
        print(f"Unread Emails               : {unread:,}")
        print(f"Subjects with 'Approved'    : {approved_count:,}")
        print(f"Approved + Time Card/FW:FYI : {len(matching_emails):,}")

    return matching_emails


if __name__ == "__main__":
    results = get_approved_cards()
    for email in results:
        print(email.Subject)