import re
from filter_service import get_approved_cards


day_block_pattern = re.compile(
    r"(?P<day>\w+,\s+\d{1,2}\s+\w+)\s*"
    r"Contractor Labor\s*-\s*(?P<labor_type>[\w\s]+?)\s*-\s*(?P<time_type>[\w\s]+?)\s*"
    r"(?P<hours>[\d.]+)\s*Hours\s*"
    r"Project\s*(?P<project_code>\d+)\s*-\s*(?P<project_name>.+?)\s*"
    r"Task\s*(?P<task>.+?)(?=\n\s*\n|\Z)",
    re.IGNORECASE | re.DOTALL
)


def extract(email):
    body = email.Body or ""

    entries = []
    for match in day_block_pattern.finditer(body):
        entries.append({
            "day": match.group("day").strip(),
            "labor_type": match.group("labor_type").strip(),
            "time_type": match.group("time_type").strip(),
            "hours": match.group("hours").strip(),
            "project_code": match.group("project_code").strip(),
            "project_name": match.group("project_name").strip(),
            "task": match.group("task").strip(),
            "subject": email.Subject or "",
            "sender": email.SenderName or "",
            "received": str(email.ReceivedTime),
        })

    return entries


def _extract_field(text, pattern):
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


if __name__ == "__main__":
    results = get_approved_cards(verbose=False)
    if results:
        sample = results[0]
        extracted = extract(sample)   # this is a LIST of day-entries

        print(f"Found {len(extracted)} entries\n")
        for entry in extracted:            # loop through each day
            for key, value in entry.items():  # loop through that day's fields
                print(f"{key}: {value}")
            print("-" * 40)
    else:
        print("No matching emails found.")