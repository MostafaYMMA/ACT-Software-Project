import os
import re
import shutil
import tempfile
import traceback
from datetime import date

from filter_service import get_approved_cards

# pdfplumber reads the text out of an expense report's PDF attachment.
# Wrapped so a missing install only disables expense-report parsing, not
# the whole module / the timecard path.
try:
    import pdfplumber
except ImportError:
    pdfplumber = None

# Per-document tracing for the expense extractor -- prints what each source
# (PDF / body) actually yielded, so a "0 lines" result says where it was lost.
EXPENSE_DEBUG = True


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


# PR_SMTP_ADDRESS MAPI property tag. Used as a fallback to resolve the
# real SMTP address for Exchange senders, where SenderEmailAddress would
# otherwise return an internal "/O=.../CN=..." legacyExchangeDN instead
# of a usable email address.
_PR_SMTP_ADDRESS = "http://schemas.microsoft.com/mapi/proptag/0x39FE001E"


def _get_sender_email(mail_item):
    """Return the sender's actual email address (e.g. someone@gmail.com)
    instead of their display name.

    - For plain SMTP accounts (Gmail, Outlook.com, IMAP, etc.),
      SenderEmailAddress already IS the email address.
    - For Exchange accounts, SenderEmailAddress instead returns an
      internal legacyExchangeDN string, so we resolve the underlying
      Exchange user's PrimarySmtpAddress, falling back to the
      PR_SMTP_ADDRESS MAPI property if that lookup fails.
    """
    try:
        if getattr(mail_item, "SenderEmailType", None) == "EX":
            try:
                exch_user = mail_item.Sender.GetExchangeUser()
                if exch_user and exch_user.PrimarySmtpAddress:
                    return exch_user.PrimarySmtpAddress
            except Exception:
                pass
            try:
                return mail_item.PropertyAccessor.GetProperty(_PR_SMTP_ADDRESS)
            except Exception:
                pass

        return mail_item.SenderEmailAddress or ""
    except Exception:
        return ""


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


def _merge_header_fields(texts):
    """Document-level name/period/person_number for one EMAIL, resolved once
    across ALL of its text sources (body + matching attachments) rather than
    separately per source -- so a day-entry's identity doesn't depend on
    which particular text blob happened to contain the labelled header line.
    PDFs squeeze "Period Person Number Time Card Status" onto one line that
    a plain-text email body covering the same timecard often doesn't
    reproduce; resolving per-source used to give the body's day-entries a
    different (blank) person_number than the attachment's day-entries for
    the same real day, so both got inserted as separate, permanently
    duplicated rows instead of one being recognized as the other's update.
    First non-empty value found wins, in text order (body first)."""
    merged = {"name": None, "period": None, "person_number": None}
    for text in texts:
        fields = _header_fields(text)
        for key, value in fields.items():
            if not merged[key] and value:
                merged[key] = value
    return merged


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
    # Use the sender's actual email address rather than the display name
    # (filter_service's "sender" is item.SenderName -- a display name).
    # Column name stays "sender"; only the value changes.
    sender = _get_sender_email(mail_item) or email.get("sender") or ""
    received = str(mail_item.ReceivedTime)
    status = email.get("status")

    texts = [mail_item.Body or ""]
    for att in email.get("attachments", []):
        if att.get("matches_keyword"):
            texts.append(att.get("text") or "")

    header_fields = _merge_header_fields(texts)

    entries = []
    for text in texts:
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


# ======================================================================
# Expense reports
# ----------------------------------------------------------------------
# A SECOND kind of email, filtered separately (filter_service.
# get_expense_reports) and extracted here into its own row shape: one
# entry per REPORT, not per expense line -- the "Expense Item" line table
# is never parsed. Layout of one report (from the PDF the filter matched):
#
#   status: Approved, submitted by:osama.khodair@oracle.com
#   approved by: jana.wille@oracle.com, approved on: 12-MAR-2026
#   * Expense Report : 301891
#   * Expense Purpose : 400380981 HLGIU-EMEA-Motel One-OPERA Cloud rollout
#   * Report Total : 38338.96 EGP
#   * Project Details : 400380981:HLGIU-EMEA-Motel One-OPERA Cloud rollout
#   Expense Item
#   Item | Expense Date | Project Task | Expense Type | Expense Amount | ...
#   1    | 02-MAR-2026  | 6.01.00:Bill.| Other Transp.| 229.98 EGP     | ...
#
# Only the header block (the four bulleted lines + the status line) is
# kept: expense_report, the project number/name (the same number:name pair
# "Expense Purpose" carries, just space- rather than colon-separated --
# "Project Details" is the reliable one to parse it off of), the report's
# total amount + currency, status and submitted_by.
# ======================================================================

# Document-level fields. Each sits on its own line in the extracted text, so
# a plain "label : value" search (value running to the end of that line) is
# enough -- no fixed multi-field layout like the timecard header needs.
_exp_status_pattern = re.compile(r"status\s*:\s*([A-Za-z]+)", re.IGNORECASE)
_exp_submitted_pattern = re.compile(r"submitted by\s*:\s*([^\s,]+)", re.IGNORECASE)
_exp_report_pattern = re.compile(r"Expense Report\s*:\s*(\d+)", re.IGNORECASE)
_exp_total_pattern = re.compile(r"Report Total\s*:\s*([\d.,]+)\s*([A-Za-z]{2,4})?", re.IGNORECASE)
# "Project Details : 400380981:HLGIU-EMEA-Motel One-OPERA Cloud rollout" --
# the number:name form of the report's "Expense Purpose".
_exp_project_details_pattern = re.compile(
    r"Project Details\s*:\s*(?P<code>\d+)\s*:\s*(?P<name>[^\r\n]+)", re.IGNORECASE
)

# Both an Expense Item line's own date ("02-MAR-2026") and a timecard day
# header's month ("Tuesday, 26 May") spell the month as a 3-letter English
# abbreviation -- one lookup covers both, case-insensitively.
_MONTH_ABBR = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
# An Expense Item row reads "1 | 02-MAR-2026 | ...": the DD-MON-YYYY dates
# after the "Expense Item" marker are that report's own per-line dates --
# nothing else in a report's text is dated this way.
_oracle_date_pattern = re.compile(r"\b(\d{1,2})-([A-Za-z]{3})-(\d{4})\b")
_expense_item_marker_pattern = re.compile(r"Expense Item", re.IGNORECASE)

def _parse_oracle_date(day_str, month_str, year_str):
    """('02', 'MAR', '2026') -> 'YYYY-MM-DD', or None if the month
    abbreviation or the resulting date isn't valid."""
    month = _MONTH_ABBR.get(month_str.strip().upper())
    if not month:
        return None
    try:
        return date(int(year_str), month, int(day_str)).isoformat()
    except ValueError:
        return None


def _expense_item_dates(chunk):
    """Every 'Expense Date' found in one report's own Expense Item table
    (the DD-MON-YYYY dates after that report's 'Expense Item' marker -- see
    _oracle_date_pattern), as 'YYYY-MM-DD' strings. [] if the table isn't
    there or none of its dates parse."""
    marker = _expense_item_marker_pattern.search(chunk or "")
    if marker is None:
        return []
    tail = chunk[marker.end():]
    dates = []
    for day_str, month_str, year_str in _oracle_date_pattern.findall(tail):
        parsed = _parse_oracle_date(day_str, month_str, year_str)
        if parsed:
            dates.append(parsed)
    return dates


def _expense_header_fields(text):
    """Pull one report's header fields out of its text chunk. Any field not
    found comes back None rather than failing the others. expense_date and
    expense_date_start are the LATEST and EARLIEST dates found among this
    report's own Expense Item lines (a report can list several, e.g. a
    multi-day trip) -- together they form the report's own span, which
    _fill_sender_timecard_match requires a timecard record's period to fully
    contain (not just touch a single day of)."""
    status = _exp_status_pattern.search(text)
    submitted = _exp_submitted_pattern.search(text)
    report = _exp_report_pattern.search(text)
    total = _exp_total_pattern.search(text)
    project = _exp_project_details_pattern.search(text)
    item_dates = _expense_item_dates(text)

    return {
        "status": status.group(1).strip().capitalize() if status else None,
        "submitted_by": submitted.group(1).strip() if submitted else None,
        "expense_report": report.group(1).strip() if report else None,
        "report_total": total.group(1).strip() if total else None,
        "report_currency": (total.group(2) or "").strip() if total else None,
        "project_code": project.group("code").strip() if project else None,
        "project_name": project.group("name").strip() if project else None,
        "expense_date": max(item_dates) if item_dates else None,
        "expense_date_start": min(item_dates) if item_dates else None,
    }


def _split_reports(text):
    """Split a document's text into one chunk per report, on each "status :"
    marker -- an email can carry several reports, and each one's own status
    line comes first, ahead of its own "Expense Report :" line (see the
    layout above). Splitting there rather than on "Expense Report :" itself
    keeps a report's status/submitted_by line from spilling into the
    PRECEDING report's chunk. Returns [text] for a single (or no) marker."""
    starts = [m.start() for m in _exp_status_pattern.finditer(text or "")]
    if len(starts) <= 1:
        return [text or ""]
    bounds = starts + [len(text)]
    return [text[bounds[i]:bounds[i + 1]] for i in range(len(starts))]


def _expense_texts(mail_item):
    """One (source_name, text) per place a report's header fields can live:
    each PDF attachment's extracted text, plus the email body itself."""
    texts = []

    if pdfplumber is not None:
        temp_dir = tempfile.mkdtemp(prefix="expense_extract_")
        try:
            attachments = mail_item.Attachments
            for i in range(1, attachments.Count + 1):
                att = attachments.Item(i)
                filename = att.FileName or ""
                if not filename.lower().endswith(".pdf"):
                    continue
                saved_path = os.path.join(temp_dir, f"{abs(hash(filename))}_{filename}")
                try:
                    att.SaveAsFile(saved_path)
                    pages_text = []
                    with pdfplumber.open(saved_path) as pdf:
                        for page in pdf.pages:
                            page_text = page.extract_text()
                            if page_text:
                                pages_text.append(page_text)
                    texts.append((filename, "\n".join(pages_text)))
                except Exception:
                    traceback.print_exc()
                finally:
                    if os.path.exists(saved_path):
                        try:
                            os.remove(saved_path)
                        except Exception:
                            traceback.print_exc()
        except Exception:
            traceback.print_exc()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    try:
        body_text = mail_item.Body or ""
    except Exception:
        body_text = ""
    if body_text:
        texts.append(("(email body)", body_text))

    return texts


def extract_expense(email):
    """
    email: a matched-email dict from filter_service.get_expense_reports()
    (has "mail_item", "subject", "sender", "received", ...).

    Returns a LIST of entries, one per expense REPORT found (an email can
    carry several), each holding that report's own number, project
    number/name, total amount + currency, status and submitter. Handles
    several reports in one email -- whether sent as separate PDFs or
    pasted inline -- by parsing each source document, and each report
    within it, on its own; a report seen in more than one source (e.g. the
    same text repeated across the PDF and the body) is only kept once.

    Linking each report to the timecard RECORD it should be billed against
    (i.e. which sender/period submission it belongs to) is NOT done here --
    see storage_service.save_expenses/_fill_sender_timecard_match, which
    matches purely on the report's sender email against the sender of each
    timecard record on file (preferring the record whose period fully
    contains this report's [expense_date_start, expense_date] span, falling
    back to that sender's most recently received record). This function
    only extracts each report's own fields.
    """
    mail_item = email["mail_item"]
    subject = email.get("subject") or ""
    # Real sender address, not the display name -- same reason as extract().
    sender = _get_sender_email(mail_item) or email.get("sender") or ""
    received = str(mail_item.ReceivedTime)

    texts = _expense_texts(mail_item)

    entries = []
    seen_reports = set()
    for source_name, text in texts:
        headers = [_expense_header_fields(chunk) for chunk in _split_reports(text)]

        if EXPENSE_DEBUG:
            print(f"    [EXP] {source_name}: text={len(text or '')} chars, "
                  f"report-headers={len(headers)}")

        for header in headers:
            report_id = header.get("expense_report")
            if not report_id or report_id in seen_reports:
                continue
            seen_reports.add(report_id)

            entry = dict(header)
            entry["subject"] = subject
            entry["sender"] = sender
            entry["received"] = received
            # The filter only ever matches approved reports, so status is
            # Approved by construction -- fall back to that if the header
            # text didn't spell it out.
            entry["status"] = header.get("status") or email.get("status") or "Approved"

            entries.append(entry)

    if EXPENSE_DEBUG:
        print(f"    [EXP] total entries extracted: {len(entries)}")
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