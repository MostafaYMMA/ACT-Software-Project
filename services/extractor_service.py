import re
from filter_service import get_approved_cards


# Matches a day-block header, e.g. "Tuesday, 26 May". Used to split the
# source text into per-day chunks -- the fields inside a chunk are then
# pulled out independently below, since PDF-extracted timecards (Oracle
# exports via pdfplumber) reorder the fields relative to a plain-text
# email body (e.g. "Hours" lands before the time-type, and the literal
# word "Project" ends up on its own line after the project code/name).
day_header_pattern = re.compile(r"(?P<day>[A-Za-z]+,\s*\d{1,2}\s+[A-Za-z]+)")

# Oracle timecard PDFs repeat the same per-day breakdown twice: once
# under "Reported time by entry date" and again under "Calculated time
# by earned date". Everything from "Calculated Time by Project..." (the
# aggregate summary table) onward is cut off so those repeats aren't
# parsed as a second, duplicate pass over the same days.
_summary_cutoff_pattern = re.compile(r"Calculated Time by Project", re.IGNORECASE)

_hours_pattern = re.compile(r"([\d.]+)\s*Hours", re.IGNORECASE)
_project_pattern = re.compile(r"(\d{5,})\s*-\s*(.+?)\s*Task\b", re.IGNORECASE | re.DOTALL)
_task_pattern = re.compile(r"Task\s+(.+)", re.IGNORECASE | re.DOTALL)

# Plain-text email bodies keep "Contractor Labor - <labor> - <time type>"
# together on one line, ahead of the hours (e.g. "Contractor Labor - ORCL
# AE - Straight Time" ... "8.00 Hours"). PDF-extracted timecards split
# them apart -- only "Contractor Labor - <labor>" appears before the
# hours, and "<time type>" shows up on its own line straight after.
_labor_time_same_line_pattern = re.compile(
    r"Contractor Labor\s*-\s*(?P<labor_type>\w+(?:\s+\w+)*?)\s*-\s*(?P<time_type>\w+(?:\s+\w+)*?)"
    r"\s*[\d.]+\s*Hours",
    re.IGNORECASE
)
_labor_only_pattern = re.compile(r"Contractor Labor\s*-\s*([^\r\n]+)", re.IGNORECASE)
_time_type_after_hours_pattern = re.compile(r"Hours\s*[\r\n]+\s*([^\r\n]+)", re.IGNORECASE)


def _day_blocks(text):
    """Split text into (day, block_text) chunks, one per day header found."""
    cutoff = _summary_cutoff_pattern.search(text)
    if cutoff:
        text = text[:cutoff.start()]

    headers = list(day_header_pattern.finditer(text))
    blocks = []
    for i, match in enumerate(headers):
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        blocks.append((match.group("day").strip(), text[start:end]))
    return blocks


def _parse_block(day, block):
    hours_match = _hours_pattern.search(block)
    project_match = _project_pattern.search(block)
    task_match = _task_pattern.search(block)

    if not (hours_match and project_match and task_match):
        return None

    same_line_match = _labor_time_same_line_pattern.search(block)
    if same_line_match:
        labor_type = same_line_match.group("labor_type").strip()
        time_type = same_line_match.group("time_type").strip()
    else:
        labor_only_match = _labor_only_pattern.search(block)
        if not labor_only_match:
            return None
        labor_type = labor_only_match.group(1).strip()
        time_type_match = _time_type_after_hours_pattern.search(block)
        time_type = time_type_match.group(1).strip() if time_type_match else ""

    project_name = re.sub(r"\bProject\b", " ", project_match.group(2), flags=re.IGNORECASE)
    project_name = re.sub(r"\s+", " ", project_name).strip()

    return {
        "day": day,
        "labor_type": labor_type,
        "time_type": time_type,
        "hours": hours_match.group(1).strip(),
        "project_code": project_match.group(1).strip(),
        "project_name": project_name,
        "task": task_match.group(1).strip(),
    }


def extract(email):
    """
    email: the matched-email dict returned by filter_service.get_approved_cards()
    (has "mail_item", "subject", "sender", "received", "attachments", ...).

    Scans the body plus the text of any attachment that matched a keyword,
    since real timecard data sometimes lives in an attached PDF/Excel file
    rather than the email body itself.
    """
    mail_item = email["mail_item"]
    subject = email.get("subject") or ""
    sender = email.get("sender") or ""
    received = str(mail_item.ReceivedTime)

    texts = [mail_item.Body or ""]
    for att in email.get("attachments", []):
        if att.get("matches_keyword"):
            texts.append(att.get("text") or "")

    entries = []
    for text in texts:
        for day, block in _day_blocks(text):
            entry = _parse_block(day, block)
            if entry is None:
                continue
            entry["subject"] = subject
            entry["sender"] = sender
            entry["received"] = received
            entries.append(entry)

    return entries


# def _extract_field(text, pattern):
#     match = re.search(pattern, text, re.IGNORECASE)
#     if match:
#         return match.group(1).strip()
#     return None


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