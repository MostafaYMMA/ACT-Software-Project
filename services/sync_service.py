import os
import shutil
import tempfile
from datetime import datetime

from filter_service import get_approved_cards, get_expense_reports
from extractor_service import extract, extract_expense
from storage_service import (
    init_db, save_cards, save_expenses,
    export_to_csv, export_invoice_lines_to_excel, export_expenses_to_csv,
    build_outgoing_snapshot, apply_incoming_snapshot, apply_rate_update,
    build_rate_update_payload, get_device_id,
    record_finalize_from_other_device, export_snapshot_rows_to_excel,
    rebuild_active_export, finalize_active_export,
)
from outlook_service import send_sync_mail, scan_sync_mails
from sharepoint_service import (
    require_sharepoint_folder, read_boundary_date, write_boundary,
    write_device_sheet, merge_device_sheets, print_workbook,
)


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
        save_cards(all_entries)  # origin defaults to THIS device -- see storage_service.save_cards
        print("Saved entries to database.")
        export_to_csv()
        export_invoice_lines_to_excel()
    else:
        print("Nothing to save.")

    # --- Expense reports: a second, independent scan of the same window ---
    # (approved expense-report emails, filtered separately from timecards).
    report("Checking inbox for approved expense reports...")
    expense_emails = get_expense_reports(
        limit=None if has_range else 500,
        start_date=start_date,
        end_date=end_date,
        print_report=False,
    )
    report(f"Approved expense reports found: {len(expense_emails)}")

    expense_entries = []
    for email in expense_emails:
        entries = extract_expense(email)
        print(f"  - '{email['subject']}' -> {len(entries)} expense line(s)")
        expense_entries.extend(entries)

    report(f"Total expense line-items extracted: {len(expense_entries)}")

    if expense_entries:
        report("Saving expenses...")
        save_expenses(expense_entries)
        print("Saved expenses to database.")
        export_expenses_to_csv()
    else:
        print("No expenses to save.")


# ----------------------------------------------------------------------
# Email/partner sync: pulling in what the other user sent, and pushing
# out what this device has scanned. See storage_service.py for why each
# of these is safe to call repeatedly / out of order / with stale mail
# still sitting in the inbox. Only used when the Settings page's Sync
# switch is on (see ui/Pages/History.py's _sync_enabled()) -- when it's
# off, local_update/local_finalize below run instead, with no mail
# involved at all.
# ----------------------------------------------------------------------

def pull_updates(progress_callback=None):
    """
    Checks for and applies any sync mail waiting from the other user
    (a full snapshot, a standalone rate edit, or a finalize notice).
    Safe to call any time, any number of times -- every kind of incoming
    message is idempotent on the receiving end.
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    report("Checking for updates from the other user...")
    messages, temp_dir = scan_sync_mails()
    try:
        applied_snapshots = 0
        applied_rates = 0
        applied_finalizes = 0

        for message in messages:
            kind = message["kind"]
            payload = message["payload"]

            if kind == "snapshot":
                result = apply_incoming_snapshot(payload)
                if result.get("applied"):
                    applied_snapshots += 1

            elif kind == "rate":
                if apply_rate_update(payload):
                    applied_rates += 1

            elif kind == "finalize":
                # Catch this device up with the sender's closing snapshot
                # FIRST, so nothing the sender scanned in their last-minute
                # check before finalizing is missed here.
                snapshot = payload.get("snapshot")
                if snapshot:
                    apply_incoming_snapshot(snapshot)

                export_name = payload.get("export_filename") or "final_export.xlsx"
                end_date = payload.get("period_end") or (payload.get("generated_at") or "")[:10]
                record_finalize_from_other_device(export_name, end_date)
                applied_finalizes += 1

                # The actual exported file the sender attached (see
                # finalize_month) -- copied next to this device's own
                # exports so it's not lost when temp_dir is cleaned up
                # below, and so the user can find it without having to
                # dig through Outlook attachments.
                for extra_path in message.get("extra_paths", []):
                    if os.path.basename(extra_path).endswith(".xlsx") or export_name in extra_path:
                        _save_incoming_export_copy(extra_path, export_name)

        report(
            f"Updates received: {applied_snapshots} snapshot(s), "
            f"{applied_rates} rate edit(s), {applied_finalizes} finalize notice(s)."
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return {"messages_seen": len(messages)}


def _save_incoming_export_copy(source_path, export_name):
    """Copies a finalize mail's attached export file into this device's
    own outputs, next to output.csv/invoice_lines.xlsx, under the name
    the sender gave it (so 'invoice_lines_2026-07.xlsx' shows up looking
    exactly like a locally-made export, not a temp-folder path)."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    dest_dir = os.path.join(base_dir, "..", "data", "received_exports")
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, export_name)
    try:
        shutil.copyfile(source_path, dest_path)
    except OSError:
        pass


def push_updates(recipient_email, project_type=None, progress_callback=None):
    """
    Mails the other user a full current-period snapshot of what THIS
    device has stored.

    Reads the database only -- it does NOT scan the inbox. Getting mail
    out of Outlook and into the database is Scan Inbox's job (sync_cards);
    doing it again here would just re-do that work on every Update, and
    slowly, for no new data.
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    report("Preparing update for the other user...")
    snapshot = build_outgoing_snapshot(project_type=project_type)
    if not snapshot["rows"]:
        report("Nothing new to send.")
        return {"sent": False, "reason": "nothing to send"}

    sent = send_sync_mail(recipient_email, "snapshot", snapshot, snapshot["seq"])
    report("Update sent." if sent else "Failed to send the update email.")
    return {"sent": sent, "rows_sent": len(snapshot["rows"])}


def update_with_other_user(recipient_email, project_type=None, progress_callback=None):
    """
    The 'Update' button on the Export History page (sync on). Three steps,
    in order:

      1. Pull anything waiting from the other user.
      2. Push this device's own data out to them. Pull-before-push so the
         snapshot built during push reflects what was just received,
         rather than a picture that's already one step behind.
      3. Top up the active export file with every approved row that isn't
         in it yet -- including whatever step 1 just brought in. Creates
         that file on the first ever Update (and on the first Update after
         a Finalize); tops the same file up on every Update after that.

    Nothing here reads Outlook for timecards -- see push_updates.

    Steps 1 and 2 are SKIPPED when recipient_email is empty (no sync
    partner configured), and a failure in either is caught rather than
    raised. Step 3 is local work on local data -- it must not be blocked
    by there being no one to sync with, or by Outlook being shut, offline,
    or slow. The two halves of the result say which parts actually ran.
    """
    pull_result = {"skipped": True}
    push_result = {"sent": False, "reason": "no sync partner"}

    if recipient_email:
        try:
            pull_result = pull_updates(progress_callback=progress_callback)
            push_result = push_updates(
                recipient_email, project_type=project_type, progress_callback=progress_callback
            )
        except Exception as exc:  # Outlook not running, COM error, mailbox refused...
            print(f"Sync with {recipient_email} failed: {exc}")
            pull_result = {"error": str(exc)}
            push_result = {"sent": False, "reason": "sync failed", "error": str(exc)}

    if progress_callback:
        progress_callback("Updating the export file...")
    export_result = rebuild_active_export(project_type=project_type)

    return {"pull": pull_result, "push": push_result, "export": export_result}


def push_rate_update(status_key, record_id, recipient_email):
    """
    Sends ONE rate edit immediately, independent of the full snapshot
    Update/Finalize flow. This is what actually closes the gap a plain
    snapshot can't: build_outgoing_snapshot only ever includes rows this
    device scanned itself (see storage_service.save_cards), so an edit
    to a rate on a record the OTHER device originally scanned would
    never go out through Update at all, on either side, ever -- it needs
    its own small standalone message instead.
    """
    payload = build_rate_update_payload(status_key, record_id)
    if payload is None:
        return False
    # seq is only meaningful for "snapshot" messages (see
    # apply_incoming_snapshot's supersede check) -- a "rate" message is
    # applied purely by comparing rate_updated_at, so seq here only needs
    # to satisfy the subject pattern, not mean anything on its own.
    return send_sync_mail(recipient_email, "rate", payload, seq=0)


def finalize_month(recipient_email, start_date, end_date, project_type=None, progress_callback=None):
    """
    The 'Finalize' button (sync on), called AFTER the user has confirmed
    in the UI. Order matters here:
      1. A full Update pass -- pull, push, and one last top-up of the
         active export file -- so nothing sent moments ago on either side
         is missed right before the boundary moves.
      2. Close that file out: log it in export_history, advance
         get_last_export_date, and clear the active-export pointer so the
         next Update opens a NEW file and starts filling that one. The
         file itself is left exactly as it is -- it is the final export;
         nothing is re-exported into a separate one.
      3. Mail the other user a "finalize" notice: the closed file itself,
         plus a closing snapshot so their data is fully caught up too.
         Their app applies this specially (see pull_updates above) --
         logged into THEIR export_history and THEIR last_export_date is
         advanced too, so the boundary is shared rather than tracked
         per-machine.

    Note there is no output_path argument: the file being finalized is
    whichever one Update has been filling, not one chosen at save time.
    Its path comes back in the return value.
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    update_with_other_user(recipient_email, project_type=project_type, progress_callback=progress_callback)

    report("Closing out the current export sheet...")
    finalized = finalize_active_export(end_date, project_type=project_type)
    output_path = finalized["path"]
    row_count = finalized["row_count"]

    # The sheet is already closed by this point, so a failure here (or no
    # partner to notify at all) must not raise -- it would leave the
    # period closed locally while the caller was told the whole thing
    # failed. It's reported back as notified=False instead.
    sent = False
    if recipient_email:
        report("Notifying the other user...")
        try:
            closing_snapshot = build_outgoing_snapshot(project_type=project_type)
            finalize_payload = {
                "device_id": get_device_id(),
                "export_filename": os.path.basename(output_path),
                "period_start": start_date,
                "period_end": end_date,
                "snapshot": closing_snapshot,
            }
            sent = send_sync_mail(
                recipient_email, "finalize", finalize_payload, closing_snapshot["seq"],
                extra_attachments=[output_path],
                note=f"Month finalized: {start_date} to {end_date}. The final export is attached.",
            )
        except Exception as exc:
            print(f"Finalize notice to {recipient_email} failed: {exc}")
        report("Finalize notice sent." if sent else "Failed to notify the other user - the export still completed locally.")
    else:
        report("No sync partner set - finalized locally only.")

    return {"row_count": row_count, "notified": sent, "path": output_path}


# ----------------------------------------------------------------------
# SharePoint file channel: Update / View Current / Finalize.
#
# A SECOND, independent sync channel from the email/partner sync above --
# reads/writes files in a shared, locally-synced OneDrive/SharePoint
# folder instead of mail (see services/sharepoint_service.py and
# SHAREPOINT_SYNC_SPEC.md). No shared mutable state with the email
# channel or the rest of the app except the DB tables themselves (which
# this channel only ever reads, never writes).
#
# Each button re-runs the cheaper one beneath it first, so the user never
# acts on stale data. Do not flatten this nesting.
# ----------------------------------------------------------------------

def sharepoint_update(folder, progress_callback=None, project_type=None):
    """
    'Update' (SharePoint section): rebuilds THIS device's own
    current_<device_id>.xlsx from the DB and writes it into the
    SharePoint folder. Idempotent -- a full rebuild every time (RULE 1 in
    SHAREPOINT_SYNC_SPEC.md), never an append, so clicking it repeatedly
    produces the identical file.
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    require_sharepoint_folder(folder)

    report("Reading the shared boundary date...")
    boundary_date = read_boundary_date(folder)

    report("Rebuilding this device's current sheet from the database...")
    snapshot = build_outgoing_snapshot(project_type=project_type, since_date=boundary_date)
    path = write_device_sheet(folder, snapshot["rows"])

    report(f"Wrote {len(snapshot['rows'])} row(s) to {os.path.basename(path)}.")
    return {"file": path, "rows": len(snapshot["rows"])}


def sharepoint_view_current(folder, progress_callback=None, project_type=None):
    """
    'View Current': runs sharepoint_update first (so this device's own
    contribution is fresh), then reads and merges+dedups every device's
    current_*.xlsx in the folder. Display only -- writes nothing to disk
    or the DB.
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    update_result = sharepoint_update(folder, progress_callback=progress_callback, project_type=project_type)

    report("Merging every device's current sheet...")
    rows, sources = merge_device_sheets(folder)
    report(f"Merged {len(rows)} row(s) from {len(sources)} device sheet(s).")

    return {"rows": rows, "sources": sources, "update": update_result}


def sharepoint_finalize(folder, progress_callback=None, project_type=None, printer=None):
    """
    'Finalize' (SharePoint section): must be called only AFTER the user
    has confirmed in the UI. Runs sharepoint_view_current first, prints
    the merged sheet literally, and
    only on a successful print advances the ONE shared boundary.json
    (RULE 2) and resets this device's sheet to empty. A failed print
    raises before any of that happens, leaving the period open (spec sec 7).
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    view = sharepoint_view_current(folder, progress_callback=progress_callback, project_type=project_type)
    rows = view["rows"]

    report("Preparing the merged sheet for printing...")
    fd, tmp_path = tempfile.mkstemp(prefix="act_sharepoint_finalize_", suffix=".xlsx")
    os.close(fd)
    try:
        export_snapshot_rows_to_excel(rows, tmp_path)
        report("Printing...")
        print_workbook(tmp_path, printer=printer)  # raises on failure -- nothing below runs
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    report("Advancing the shared boundary...")
    new_boundary = datetime.now().strftime("%Y-%m-%d")
    write_boundary(folder, new_boundary)

    report("Resetting this device's current sheet...")
    sharepoint_update(folder, progress_callback=progress_callback, project_type=project_type)

    return {"printed": True, "boundary_date": new_boundary, "rows_printed": len(rows)}


# ----------------------------------------------------------------------
# Local-only equivalents, used when the "Sync" switch on the Settings
# page is turned off (see ui/Pages/Settings.py's SYNC_ENABLED_KEY and
# ui/Pages/History.py's _sync_enabled()). Same end results as the
# functions above -- new mail gets scanned in, a Finalize still exports
# and closes out the period locally -- just with no pull from, push to,
# or notification of another device, and therefore no partner email
# required at all.
# ----------------------------------------------------------------------

def local_update(progress_callback=None):
    """Sync-off equivalent of the 'Update' button: scans this device's
    own inbox (same as Scan Inbox), then tops up the active export sheet
    with whatever that scan brought in -- with no pull from or push to
    another device. The export half is the same rolling sheet the sync-on
    Update fills (see update_with_other_user), so turning sync off doesn't
    put the app on a different export file."""
    sync_cards(progress_callback=progress_callback)

    if progress_callback:
        progress_callback("Updating the export file...")
    export_result = rebuild_active_export()

    return {"scanned": True, "export": export_result}


def local_finalize(start_date, end_date, project_type=None, progress_callback=None):
    """Sync-off equivalent of 'Finalize': scans this device's own inbox,
    then closes out the active export sheet locally -- no pull, no
    notification. As with finalize_month there is no output_path: the
    file closed is whichever one Update has been filling. notified is
    deliberately None (not False) so the UI can tell "sync is off,
    nothing was attempted" apart from "sync is on and the notification
    failed to send"."""

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    sync_cards(progress_callback=progress_callback)

    report("Closing out the current export sheet...")
    finalized = finalize_active_export(end_date, project_type=project_type)

    return {"row_count": finalized["row_count"], "notified": None, "path": finalized["path"]}
