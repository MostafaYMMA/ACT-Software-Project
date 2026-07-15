import bisect
import os
import re
import shutil
import tempfile
import traceback

from filter_service import get_approved_cards

# pdfplumber is the one library the expense line-item table needs (its
# extract_tables() gives clean per-cell values, which flat text can't --
# "Project Task" and "Expense Type" are both multi-word with no delimiter
# between them). Wrapped so a missing install only disables expense
# line-item parsing, not the whole module / the timecard path.
try:
    import pdfplumber
except ImportError:
    pdfplumber = None

# BeautifulSoup lets us read <table> cells out of an HTML email body, for
# expense reports pasted inline into a (forwarded) email rather than attached
# as a PDF. Optional -- its absence just disables that one source.
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

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


# ======================================================================
# Expense reports
# ----------------------------------------------------------------------
# A SECOND kind of email, filtered separately (filter_service.
# get_expense_reports) and extracted here into its own row shape. Kept in
# this same module as the timecard logic on purpose -- see extract_expense
# below. Layout of one report (from the PDF the filter matched):
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
# The four bulleted lines + the status/approver header are document-level
# (one per report); the "Expense Item" table is one row per line, and each
# line becomes one entry with the header fields stamped onto it -- exactly
# how _header_fields is stamped onto every timecard day-block above.
# ======================================================================

# Document-level fields. Each sits on its own line in the extracted text, so
# a plain "label : value" search (value running to the end of that line) is
# enough -- no fixed multi-field layout like the timecard header needs.
_exp_status_pattern = re.compile(r"status\s*:\s*([A-Za-z]+)", re.IGNORECASE)
_exp_submitted_pattern = re.compile(r"submitted by\s*:\s*([^\s,]+)", re.IGNORECASE)
_exp_approved_by_pattern = re.compile(r"approved by\s*:\s*([^\s,]+)", re.IGNORECASE)
_exp_approved_on_pattern = re.compile(r"approved on\s*:\s*([0-9A-Za-z-]+)", re.IGNORECASE)
_exp_report_pattern = re.compile(r"Expense Report\s*:\s*(\d+)", re.IGNORECASE)
_exp_total_pattern = re.compile(r"Report Total\s*:\s*([\d.,]+)\s*([A-Za-z]{2,4})?", re.IGNORECASE)
# "Project Details : 400380981:HLGIU-EMEA-Motel One-OPERA Cloud rollout" --
# the number:name form you asked for (Expense Purpose has the same pair but
# space-separated, so Project Details is the reliable one to key off).
_exp_project_details_pattern = re.compile(
    r"Project Details\s*:\s*(?P<code>\d+)\s*:\s*(?P<name>[^\r\n]+)", re.IGNORECASE
)
# An amount cell reads "229.98 EGP" -- split the number off its currency.
_exp_amount_pattern = re.compile(r"([\d.,]+)\s*([A-Za-z]{2,4})?")


def _expense_header_fields(text):
    """Pull the one-per-report fields out of the report text. Any field not
    found comes back None rather than failing the others."""
    status = _exp_status_pattern.search(text)
    submitted = _exp_submitted_pattern.search(text)
    approved_by = _exp_approved_by_pattern.search(text)
    approved_on = _exp_approved_on_pattern.search(text)
    report = _exp_report_pattern.search(text)
    total = _exp_total_pattern.search(text)
    project = _exp_project_details_pattern.search(text)

    return {
        "status": status.group(1).strip().capitalize() if status else None,
        "submitted_by": submitted.group(1).strip() if submitted else None,
        "approved_by": approved_by.group(1).strip() if approved_by else None,
        "approved_on": approved_on.group(1).strip() if approved_on else None,
        "expense_report": report.group(1).strip() if report else None,
        "report_total": total.group(1).strip() if total else None,
        "report_currency": (total.group(2) or "").strip() if total else None,
        "project_code": project.group("code").strip() if project else None,
        "project_name": project.group("name").strip() if project else None,
    }


# The Expense Item table's column headings, lower-cased, mapped to the entry
# key each fills. Matching by heading (not column position) means a report
# that reorders or adds columns still lines up.
_EXPENSE_COLUMN_MAP = {
    "item": "item",
    "expense date": "date",
    "project task": "task",
    "expense type": "expense_type",
    "expense amount": "_amount_raw",   # "229.98 EGP" -- split below
    "expense description": "description",
    "expense receipts": "receipt",
}


def _is_expense_item_table(table):
    """True if `table`'s header row is the Expense Item table (carries the
    Expense Date + Project Task columns) rather than some other table in the
    same document -- e.g. the timecard grid in a combined 'Timecard & expenses'
    email, which must be ignored here."""
    if not table or not table[0]:
        return False
    header = [(cell or "").strip().lower() for cell in table[0]]
    return "expense date" in header and "project task" in header


def _rows_to_line_items(table):
    """Map one Expense Item table's data rows to per-line dicts, keyed by
    column heading (so a reordered/extra column still lines up)."""
    header = [(cell or "").strip().lower() for cell in table[0]]
    col_keys = {i: _EXPENSE_COLUMN_MAP[name] for i, name in enumerate(header)
                if name in _EXPENSE_COLUMN_MAP}

    line_items = []
    for row in table[1:]:
        if row is None:
            continue
        entry = {}
        for i, key in col_keys.items():
            value = row[i] if i < len(row) else None
            entry[key] = (value or "").strip().replace("\n", " ") if value else None

        # A wholly empty row (all cells None) is table padding, not a line.
        if not any(entry.values()):
            continue

        amount_raw = entry.pop("_amount_raw", None)
        if amount_raw:
            amount_match = _exp_amount_pattern.search(amount_raw)
            if amount_match:
                entry["amount"] = amount_match.group(1)
                entry["currency"] = (amount_match.group(2) or "").strip()
        line_items.append(entry)
    return line_items


def _split_reports(text):
    """Split a document's text into one chunk per report, on each "Expense
    Report :" marker -- an email can carry several reports, and each keeps its
    own project/approver header this way. Returns [text] for a single (or no)
    marker."""
    starts = [m.start() for m in re.finditer(r"Expense Report\s*:", text or "", re.IGNORECASE)]
    if len(starts) <= 1:
        return [text or ""]
    bounds = starts + [len(text)]
    return [text[bounds[i]:bounds[i + 1]] for i in range(len(starts))]


def _tables_from_pdf_page(page):
    """Tables on a PDF page: ruled-line detection first, then a text-alignment
    fallback. Oracle expense PDFs are often shaded rather than ruled, and the
    default (lines) strategy finds no table at all on those -- which is exactly
    how a report can match the filter yet extract zero lines."""
    tables = page.extract_tables() or []
    if not tables:
        tables = page.extract_tables({
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
        }) or []
    return tables


# ----------------------------------------------------------------------
# Word-coordinate fallback for the Expense Item table.
#
# Both of pdfplumber's table strategies can come up empty on Oracle's
# expense PDFs (shaded rows, no ruling lines, columns not aligned tightly
# enough for the text strategy) -- the live failure mode was exactly this:
# page text and header extracted fine, item-tables=0. The words themselves
# still carry exact x/y coordinates though, so the table is rebuilt from
# those directly: find the header row, take each column heading's x-center,
# and bucket every following row's words into the nearest column.
# ----------------------------------------------------------------------

# Canonical header row, in table order. Lower-cased these are exactly the
# _EXPENSE_COLUMN_MAP keys, so a rebuilt table flows through
# _is_expense_item_table/_rows_to_line_items like a native pdfplumber one.
_EXPENSE_TABLE_COLUMNS = [
    "Item", "Expense Date", "Project Task", "Expense Type",
    "Expense Amount", "Expense Description", "Expense Receipts",
]

_LINE_TOP_TOLERANCE = 3  # words within this many points of each other share a line


def _lines_from_words(words):
    """Group pdfplumber words into visual lines (top-to-bottom, each line's
    words left-to-right) by their 'top' coordinate."""
    lines = []
    current = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if current and abs(word["top"] - current[0]["top"]) > _LINE_TOP_TOLERANCE:
            lines.append(sorted(current, key=lambda w: w["x0"]))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(sorted(current, key=lambda w: w["x0"]))
    return lines


def _find_header_columns(line_words):
    """If this line is the Expense Item header row, return the x-center of
    each column heading (in _EXPENSE_TABLE_COLUMNS order); else None. Matches
    the headings as word sequences, left to right, so 'Expense Date' is found
    as the pair of words 'Expense','Date' at their actual positions."""
    tokens = [w["text"].strip().lower() for w in line_words]
    centers = []
    i = 0
    for column in _EXPENSE_TABLE_COLUMNS:
        phrase = column.lower().split()
        found = None
        for j in range(i, len(tokens) - len(phrase) + 1):
            if tokens[j:j + len(phrase)] == phrase:
                found = j
                break
        if found is None:
            return None
        first, last = line_words[found], line_words[found + len(phrase) - 1]
        centers.append((first["x0"] + last["x1"]) / 2)
        i = found + len(phrase)
    return centers


def _expense_rows_from_words(page, carry_centers=None):
    """Rebuild the Expense Item table rows on one page from word coordinates.

    Returns (rows, centers, continued, still_open):
      rows       -- list of 7-cell row lists (no header row included)
      centers    -- the column x-centers in effect, for carrying to the next page
      continued  -- True when the rows continue a table begun on an earlier
                    page (no header here; carry_centers was used)
      still_open -- True when the page ended while still inside the table
                    (no non-row line after it), i.e. it may spill onto the
                    next page

    Row rules: a line whose Item cell is an integer starts a new row; a line
    with an empty Item cell continues the previous row (wrapped cell text);
    anything else ends the table (footer text and the like).
    """
    try:
        words = page.extract_words()
    except Exception:
        traceback.print_exc()
        return [], None, False, False
    if not words:
        return [], None, False, False

    lines = _lines_from_words(words)

    centers = None
    header_index = -1
    for index, line in enumerate(lines):
        centers = _find_header_columns(line)
        if centers is not None:
            header_index = index
            break

    continued = False
    if centers is None:
        if carry_centers is None:
            return [], None, False, False
        centers = carry_centers
        continued = True

    # Column boundaries sit midway between adjacent heading centers; a word
    # belongs to whichever column its own center falls inside. This is what
    # makes the split robust with no delimiter between 'Project Task' text
    # and 'Expense Type' text -- their x-positions decide.
    boundaries = [(centers[k] + centers[k + 1]) / 2 for k in range(len(centers) - 1)]

    rows = []
    still_open = True
    for line in lines[header_index + 1:]:
        cells = [[] for _ in centers]
        for word in line:
            column = bisect.bisect_right(boundaries, (word["x0"] + word["x1"]) / 2)
            cells[column].append(word["text"])
        cells = [" ".join(cell).strip() for cell in cells]

        if re.fullmatch(r"\d+", cells[0] or ""):
            rows.append(cells)
        elif rows and not cells[0]:
            # Wrapped cell text: fold each non-empty cell into the row above.
            for k, cell in enumerate(cells):
                if cell:
                    rows[-1][k] = f"{rows[-1][k]} {cell}".strip()
        else:
            still_open = False  # footer/next section -- the table is over
            break

    if continued and not rows:
        return [], None, False, False
    return rows, centers, continued, still_open


def _html_tables(html):
    """Every <table> in an HTML body as a list of row-cell-lists -- for
    expense reports pasted inline into a (forwarded) email instead of attached
    as a PDF. Real <td> boundaries separate the columns cleanly, the same way
    pdfplumber's cells do."""
    if not html or BeautifulSoup is None:
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []
    tables = []
    for table_el in soup.find_all("table"):
        rows = []
        for tr in table_el.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _expense_documents(mail_item):
    """One (source_name, header_text, tables) per place a report can live: each
    PDF attachment, plus the email body itself (its plain text for the header
    regex, and any HTML <table> for the line items). Parsing per document keeps
    several forwarded reports apart instead of merging them."""
    documents = []

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
                    texts, tables, word_tables = [], [], []
                    carry_centers = None  # column x-centers of a table spilling across pages
                    with pdfplumber.open(saved_path) as pdf:
                        for page in pdf.pages:
                            page_text = page.extract_text()
                            if page_text:
                                texts.append(page_text)
                            page_tables = _tables_from_pdf_page(page)
                            tables.extend(page_tables)
                            if any(_is_expense_item_table(t) for t in page_tables):
                                carry_centers = None  # real detection worked here
                                continue
                            # Table detection came up empty -- rebuild from word
                            # coordinates instead (the Oracle shaded-table case).
                            rows, centers, continued, still_open = _expense_rows_from_words(
                                page, carry_centers
                            )
                            if rows:
                                if continued and word_tables:
                                    word_tables[-1].extend(rows)
                                else:
                                    word_tables.append([list(_EXPENSE_TABLE_COLUMNS)] + rows)
                            carry_centers = centers if (still_open and centers is not None) else None
                    tables.extend(word_tables)
                    documents.append((filename, "\n".join(texts), tables))
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

    # The body always gets one more shot -- plain text for the header regex,
    # HTMLBody for any inline table (the forwarded-inline case).
    try:
        body_text = mail_item.Body or ""
    except Exception:
        body_text = ""
    try:
        html_body = mail_item.HTMLBody or ""
    except Exception:
        html_body = ""
    body_tables = _html_tables(html_body)
    if body_text or body_tables:
        documents.append(("(email body)", body_text, body_tables))

    return documents


def extract_expense(email):
    """
    email: a matched-email dict from filter_service.get_expense_reports()
    (has "mail_item", "subject", "sender", "received", ...).

    Returns a LIST of entries, one per line in each report's Expense Item
    table, with that report's header fields (project, status, submitter/
    approver) stamped on plus its own Expense Date. Shaped to sit alongside the
    timecard entries so the two can be joined on project_code.

    Handles several reports in one email -- whether sent as separate PDFs or
    pasted inline -- by parsing each source document, and each report within
    it, on its own.
    """
    mail_item = email["mail_item"]
    subject = email.get("subject") or ""
    # Real sender address, not the display name -- same reason as extract().
    sender = _get_sender_email(mail_item) or email.get("sender") or ""
    received = str(mail_item.ReceivedTime)

    entries = []
    for source_name, text, tables in _expense_documents(mail_item):
        item_tables = [t for t in tables if _is_expense_item_table(t)]
        headers = [_expense_header_fields(chunk) for chunk in _split_reports(text)]

        if EXPENSE_DEBUG:
            print(f"    [EXP] {source_name}: text={len(text or '')} chars, "
                  f"item-tables={len(item_tables)}, report-headers={len(headers)}")

        for t_index, table in enumerate(item_tables):
            # Pair each table with its own report's header when the counts line
            # up (several reports side by side); otherwise the single/first
            # header covers the whole document.
            if len(headers) == len(item_tables):
                header = headers[t_index]
            elif headers:
                header = headers[0]
            else:
                header = {}

            for line in _rows_to_line_items(table):
                entry = {**header, **line}
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