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


# Two layouts both appear in the wild: plain-body/PDF text puts the
# number first ("8.00 Hours"), but some forwarded Time Card emails
# reverse it ("Hours: 8.00"). Group 1 covers the first, group 2 the
# second -- _parse_block below takes whichever one matched.
_hours_pattern = re.compile(r"([\d.]+)\s*Hours\b|\bHours:\s*([\d.]+)", re.IGNORECASE)
_project_pattern = re.compile(r"(\d{5,})\s*-\s*(.+?)\s*Task\b", re.IGNORECASE | re.DOTALL)

# Non-greedy, stopping at the first blank line (or end of block): some
# templates put a "________________________________" divider right
# after the task name, still inside the same day's block -- a greedy
# capture would swallow that divider into the task name too.
_task_pattern = re.compile(r"Task\s+(.+?)(?=\r?\n\s*\r?\n|\Z)", re.IGNORECASE | re.DOTALL)

# Plain-text email bodies keep "Contractor Labor - <labor> - <time type>"
# together on one line, ahead of the hours (e.g. "Contractor Labor - ORCL
# AE - Straight Time" ... "8.00 Hours", or the reversed "Hours: 8.00").
# PDF-extracted timecards split them apart -- only "Contractor Labor -
# <labor>" appears before the hours, and "<time type>" shows up on its
# own line straight after (handled by the _labor_only_pattern fallback
# below instead).
_labor_time_same_line_pattern = re.compile(
    r"Contractor Labor\s*-\s*(?P<labor_type>\w+(?:\s+\w+)*?)\s*-\s*(?P<time_type>\w+(?:\s+\w+)*?)"
    r"\s*(?:[\d.]+\s*Hours\b|Hours:\s*[\d.]+)",
    re.IGNORECASE
)
_labor_only_pattern = re.compile(r"Contractor Labor\s*-\s*([^\r\n]+)", re.IGNORECASE)
_time_type_after_hours_pattern = re.compile(r"Hours\s*[\r\n]+\s*([^\r\n]+)", re.IGNORECASE)

# Document-level fields (one per email/attachment, not per day). The name
# has no label -- it's just whatever line sits right after "Time card".
# Period/Person Number use \s+ rather than a fixed layout because plain
# email bodies put each label and value on its own line, while PDFs
# squeeze the labels onto one line and the values onto the next -- \s+
# matches either a single space or a run of blank lines, so one pattern
# covers both.
_name_pattern = re.compile(r"Time card\s*[\r\n]+\s*([^\r\n]+)", re.IGNORECASE)
_period_person_pattern = re.compile(
    r"Period\s+Person Number\s+Time Card Status\s+"
    r"(?P<period>\d{1,2}/\d{1,2}/\d{2,4}\s*-\s*\d{1,2}/\d{1,2}/\d{2,4})\s+"
    r"(?P<person_number>\d+)",
    re.IGNORECASE
)


def _header_fields(text):
    """Pull the document-level name/period/person-number out of one text
    blob (email body or attachment text). Returns None for any field not
    found rather than failing the whole lookup."""
    name_match = _name_pattern.search(text)
    period_person_match = _period_person_pattern.search(text)

    return {
        "name": name_match.group(1).strip() if name_match else None,
        "period": period_person_match.group("period").strip() if period_person_match else None,
        "person_number": period_person_match.group("person_number").strip() if period_person_match else None,
    }


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
        "hours": (hours_match.group(1) or hours_match.group(2)).strip(),
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
    status = email.get("status")

    texts = [mail_item.Body or ""]
    for att in email.get("attachments", []):
        if att.get("matches_keyword"):
            texts.append(att.get("text") or "")

    entries = []
    for text in texts:
        header_fields = _header_fields(text)
        for day, block in _day_blocks(text):
            entry = _parse_block(day, block)
            if entry is None:
                continue
            entry["name"] = header_fields["name"]
            entry["period"] = header_fields["period"]
            entry["person_number"] = header_fields["person_number"]
            entry["subject"] = subject
            entry["sender"] = sender
            entry["received"] = received
            entry["status"] = status
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