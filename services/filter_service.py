pip install pywin32


# Connect to Outlook
outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")

# 6 = Inbox
inbox = outlook.GetDefaultFolder(6)

# Counters
total = 0
read = 0
unread = 0
approved_count = 0
matching_count = 0

# Regular expressions
approved_pattern = re.compile(r"\bapproved\b", re.IGNORECASE)

subject_pattern = re.compile(
    r"\btime(?:\s+|-)?card\b|^\s*FW:\s*FYI:?",
    re.IGNORECASE
)

for item in inbox.Items:
    try:
        # Only MailItem objects
        if item.Class != 43:
            continue

        total += 1

        if item.UnRead:
            unread += 1
        else:
            read += 1

        subject = item.Subject or ""

        # Count every subject containing "Approved"
        if approved_pattern.search(subject):
            approved_count += 1

        # Count Approved + (Time Card OR FW: FYI)
        if approved_pattern.search(subject) and subject_pattern.search(subject):
            matching_count += 1
            print(subject)

    except Exception as e:
        print(f"Error: {e}")

print("\n========== RESULTS ==========")
print(f"Total Emails                : {total:,}")
print(f"Read Emails                 : {read:,}")
print(f"Unread Emails               : {unread:,}")
print(f"Subjects with 'Approved'    : {approved_count:,}")
print(f"Approved + Time Card/FW:FYI : {matching_count:,}")
 
