import os
import shutil
import tempfile
import traceback
from datetime import datetime, timedelta

from filter_service import get_approved_cards, get_expense_reports, get_newest_received
from extractor_service import extract, extract_expense
from storage_service import (
    init_db, save_cards, save_expenses,
    export_to_csv, export_invoice_lines_to_excel, export_expenses_to_csv,
    build_outgoing_snapshot, apply_incoming_snapshot, apply_rate_update,
    build_rate_update_payload, get_device_id,
    record_finalize_from_other_device, export_snapshot_rows_to_excel,
    rebuild_active_export, finalize_active_export,
    relocate_last_export_history_entry,
    get_last_scan_time, set_last_scan_time,
)
from outlook_service import send_sync_mail, scan_sync_mails, read_collected_sync_mails
from mail_export import archive_matched_email, TIMECARDS_ARCHIVE_DIR, EXPENSES_ARCHIVE_DIR
from sharepoint_service import (
    require_sharepoint_folder, read_boundary_date, write_boundary,
    write_device_sheet, merge_device_sheets, print_workbook,
)


# How far BEHIND the stored high-water mark each scan actually starts.
#
# Outlook can put an email in the folder after a scan has already passed
# that point in time: a mailbox that was offline syncing down a backlog, a
# slow IMAP/POP account, or mail dragged in from another folder all arrive
# late carrying their ORIGINAL, older ReceivedTime. A scan starting exactly
# at the mark would step over those forever. Re-walking a few minutes of
# already-seen mail costs a little time and nothing else -- storage_service
# upserts on (day, project, task, person, month), so a re-scanned email
# lands on the row it already wrote rather than duplicating it.
SCAN_OVERLAP = timedelta(minutes=10)

# What the very first scan on a device covers, before there's any mark to
# work from. Unchanged from the old no-range default.
FIRST_SCAN_LIMIT = 500


def sync_cards(progress_callback=None):
    """
    Pull approved timecard emails, extract entries, and persist them.

    Incremental: the scan starts from where the previous one finished
    (storage_service.get_last_scan_time, minus SCAN_OVERLAP) rather than
    re-reading the whole inbox, so a second Scan Inbox straight after the
    first has almost nothing to walk. The mark is only moved once the scan
    below completes -- a scan that dies partway through leaves it where it
    was, and the next one re-covers the same ground.

    On a device that has never scanned, there is no mark yet and the
    FIRST_SCAN_LIMIT most recently received emails are scanned instead.

    There is no date-range argument: the range is always "since last time".
    The Dashboard's period picker filters what's DISPLAYED, not what's
    scanned (see ui/Pages/Dashboard.py).
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    init_db()

    # Read up front, applied at the end -- see filter_service.get_newest_received
    # for why the order matters.
    newest_in_folder = get_newest_received()

    last_scan = get_last_scan_time()
    start_date = last_scan - SCAN_OVERLAP if last_scan else None
    if start_date:
        report(f"Scanning inbox for mail received since {start_date:%Y-%m-%d %H:%M:%S}...")
    else:
        report(f"First scan - checking the {FIRST_SCAN_LIMIT} most recent emails...")

    report("Checking inbox for approved timecards...")
    sync_mail_items = []
    emails = get_approved_cards(
        limit=None if start_date else FIRST_SCAN_LIMIT,
        start_date=start_date,
        sync_mail_items=sync_mail_items,
    )
    report(f"Approved emails found: {len(emails)}")

    all_entries = []
    for email in emails:
        if email.get("status") == "Approved":
            try:
                archive_matched_email(
                    email["mail_item"], email["received"],
                    TIMECARDS_ARCHIVE_DIR, "timecard",
                )
            except Exception:
                traceback.print_exc()
        entries = extract(email)
        print(f"  - '{email['subject']}' -> {len(entries)} entries")
        all_entries.extend(entries)

    report(f"Total entries extracted: {len(all_entries)}")

    if all_entries:
        report("Saving entries...")
        save_cards(all_entries)  # origin defaults to THIS device -- see storage_service.save_cards
        print("Saved entries to database.")
        export_to_csv()
        #export_invoice_lines_to_excel() Hashed to not create the invoice lines file on every scan, only when the user clicks the Export button.
    else:
        print("Nothing to save.")

    # --- Expense reports: a second, independent scan of the same window ---
    # (approved expense-report emails, filtered separately from timecards).
    report("Checking inbox for approved expense reports...")
    expense_emails = get_expense_reports(
        limit=None if start_date else FIRST_SCAN_LIMIT,
        start_date=start_date,
        print_report=False,
    )
    report(f"Approved expense reports found: {len(expense_emails)}")

    expense_entries = []
    for email in expense_emails:
        # process_email_expense's match logic requires "Approved" to be
        # one of the three terms present -- every match here is already
        # an approved expense report, no separate status field to check.
        try:
            archive_matched_email(
                email["mail_item"], email["received"],
                EXPENSES_ARCHIVE_DIR, "expense",
            )
        except Exception:
            traceback.print_exc()
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

    # --- Cross-device sync mail, picked up from the SAME Outlook walk as
    # the timecard scan above (sync_mail_items), not a second one. A
    # failure here (Outlook hiccup, unreadable attachment, no sync
    # partner's mail waiting at all) must not undo the timecard/expense
    # saves that already happened above -- caught and reported, never
    # raised.
    try:
        if sync_mail_items:
            report("Checking for updates from the other user...")
        pull_collected_updates(sync_mail_items, progress_callback=progress_callback)
    except Exception as exc:
        print(f"Sync-mail check failed: {exc}")

    # Both halves got through -- everything up to newest_in_folder has now
    # been looked at, so the next scan can start there. Last thing in the
    # function on purpose: an exception anywhere above skips this and the
    # next scan re-covers the same window rather than losing it.
    set_last_scan_time(newest_in_folder)


# ----------------------------------------------------------------------
# Email/partner sync: pulling in what the other user sent, and pushing
# out what this device has scanned. See storage_service.py for why each
# of these is safe to call repeatedly / out of order / with stale mail
# still sitting in the inbox. Driven by the Current Sheet page's Sync
# button (UpdateWorker, ui/sync_workers.py) and by Finalize when a sync
# partner is configured -- local_finalize below runs instead when there
# is no partner, with no mail involved at all.
# ----------------------------------------------------------------------

def _apply_sync_messages(messages):
    """
    Shared apply logic behind pull_updates (its own dedicated mail scan)
    and pull_collected_updates (mail items a regular Scan Inbox pass
    already walked past) -- one implementation of "what an incoming sync
    message does to the local DB", reused by both instead of forked.

    Returns (applied_snapshots, applied_rates, applied_finalizes) counts.
    """
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
            # exports so it's not lost when temp_dir is cleaned up by the
            # caller, and so the user can find it without having to dig
            # through Outlook attachments.
            for extra_path in message.get("extra_paths", []):
                if os.path.basename(extra_path).endswith(".xlsx") or export_name in extra_path:
                    _save_incoming_export_copy(extra_path, export_name)

    return applied_snapshots, applied_rates, applied_finalizes


def pull_updates(progress_callback=None):
    """
    Checks for and applies any sync mail waiting from the other user
    (a full snapshot, a standalone rate edit, or a finalize notice), via
    its OWN dedicated Outlook folder walk (scan_sync_mails). Safe to call
    any time, any number of times -- every kind of incoming message is
    idempotent on the receiving end.

    Fallback / manual-trigger path only -- it has no live callers. The
    Update/Sync button (update_with_other_user) is push-only and does NOT
    pull; Scan Inbox pulls via pull_collected_updates, which reuses mail
    items the regular scan already walked past rather than doing a second,
    redundant walk just for this. Both paths share _apply_sync_messages.
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    report("Checking for updates from the other user...")
    messages, temp_dir = scan_sync_mails()
    try:
        applied_snapshots, applied_rates, applied_finalizes = _apply_sync_messages(messages)
        report(
            f"Updates received: {applied_snapshots} snapshot(s), "
            f"{applied_rates} rate edit(s), {applied_finalizes} finalize notice(s)."
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return {"messages_seen": len(messages)}


def pull_collected_updates(sync_mail_items, progress_callback=None):
    """
    Same as pull_updates, but over sync-mail items a regular Scan Inbox
    pass already collected in the SAME Outlook walk (see
    filter_service.get_approved_cards' sync_mail_items param) instead of
    doing a second, dedicated walk. Called from sync_cards.

    sync_mail_items is typically empty or has just a handful of items --
    that's the normal case, not an error; this returns cleanly either way
    ({"messages_seen": 0} when nothing was waiting).
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    messages, temp_dir = read_collected_sync_mails(sync_mail_items)
    try:
        if not messages:
            return {"messages_seen": 0}

        applied_snapshots, applied_rates, applied_finalizes = _apply_sync_messages(messages)
        report(
            f"Updates received from the other user: {applied_snapshots} snapshot(s), "
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
    The 'Sync' button (Current Sheet / Export History, sync on). Two
    steps, in order:

      1. Push this device's own data out to the other user by email.
      2. Top up the active export file with every approved row that isn't
         in it yet. Creates that file on the first ever Update (and on
         the first Update after a Finalize); tops the same file up on
         every Update after that.

    This does NOT pull/check for incoming sync mail -- that is Scan
    Inbox's job now (see sync_service.sync_cards' call to
    pull_collected_updates), which picks up any waiting sync mail from
    the SAME Outlook walk as the regular timecard scan. Sync doing its
    own separate pull here used to mean every Sync click also silently
    re-walked the whole inbox looking for updates ("Checking for updates
    from the other user...") even though the button's only job, from the
    user's perspective, is to SEND. Keeping Sync as send-only here avoids
    that redundant walk and matches what Scan Inbox already covers.

    Step 1 is SKIPPED when recipient_email is empty (no sync partner
    configured), and a failure in it is caught rather than raised. Step 2
    is local work on local data -- it must not be blocked by there being
    no one to sync with, or by Outlook being shut, offline, or slow.
    """
    push_result = {"sent": False, "reason": "no sync partner"}

    if recipient_email:
        try:
            push_result = push_updates(
                recipient_email, project_type=project_type, progress_callback=progress_callback
            )
        except Exception as exc:  # Outlook not running, COM error, mailbox refused...
            print(f"Sync with {recipient_email} failed: {exc}")
            push_result = {"sent": False, "reason": "sync failed", "error": str(exc)}

    if progress_callback:
        progress_callback("Updating the export file...")
    export_result = rebuild_active_export(project_type=project_type)

    return {"push": push_result, "export": export_result}


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


def _copy_finalized_export(internal_path, destination_path):
    """
    Copies a just-closed Finalize export from its internal, auto-generated
    path (see storage_service._new_active_export_path) to the destination
    the user chose in a Save As dialog, and repoints export_history at
    that destination (storage_service.relocate_last_export_history_entry)
    so Export History's double-click finds the file where the user can
    actually still find it, not at the internal path they never see.

    A COPY, not a move -- the internal file is what the rolling-export
    model treats as the closed sheet's canonical record (see the module
    docstring above rebuild_active_export in storage_service.py), and
    finalize_month's caller still needs it to exist afterward (it's
    mailed to the other user as an attachment). Leaving it in place also
    means a failed copy can never destroy the one copy of the data that
    is already known-good -- worst case there end up being two copies of
    the same file, never zero.

    Raises RuntimeError (not swallowed) if the copy fails. By the time
    this runs, finalize_active_export has already committed -- the
    period IS closed -- so a copy failure can't be walked back into "the
    whole finalize failed" without restructuring that model (out of
    scope here). Instead this reports clearly that the export is safely
    finalized at internal_path, just not also at the requested
    destination, so the caller is never told a plain, unqualified
    success when the destination it asked for doesn't actually have the
    file.
    """
    try:
        shutil.copy2(internal_path, destination_path)
    except OSError as exc:
        raise RuntimeError(
            f"The export was finalized and closed successfully at:\n\n{internal_path}\n\n"
            f"but could not be copied to your chosen location:\n\n{destination_path}\n\n"
            f"Reason: {exc}\n\n"
            "Your data is safe at the internal path above -- you can copy it there "
            "yourself, or leave it; Export History still lists it."
        ) from exc

    relocate_last_export_history_entry(internal_path, destination_path)
    return destination_path


def finalize_month(
    recipient_email, start_date, end_date, project_type=None, progress_callback=None, save_as_path=None,
):
    """
    The 'Finalize' button (sync on), called AFTER the user has confirmed
    in the UI. Order matters here:
      1. A full Update pass -- push and one last top-up of the active
         export file (update_with_other_user is push-only; it does not
         pull the partner's last-minute edits before the boundary moves).
      2. Close that file out: log it in export_history, advance
         get_last_export_date, and clear the active-export pointer so the
         next Update opens a NEW file and starts filling that one. The
         file itself is left exactly as it is -- it is the final export;
         nothing is re-exported into a separate one.
      3. If save_as_path is given (the UI's Save As dialog -- shown BEFORE
         this function is ever called, so a cancelled dialog means this
         is never reached at all), copy the closed file there and
         repoint export_history at it -- see _copy_finalized_export.
      4. Mail the other user a "finalize" notice: the closed file itself,
         plus a closing snapshot so their data is fully caught up too.
         Their app applies this specially (via pull_collected_updates ->
         _apply_sync_messages, on their next Scan Inbox) -- logged into
         THEIR export_history and THEIR last_export_date is advanced too,
         so the boundary is shared rather than tracked per-machine.

    Note there is no output_path argument: the file being finalized is
    whichever one Update has been filling, not one chosen at save time --
    save_as_path only says where a COPY of it should also land. Its path
    (the save_as_path destination, if one was given, else the internal
    path) comes back in the return value.
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

    if save_as_path:
        report("Saving the finalized export to the chosen location...")
        output_path = _copy_finalized_export(output_path, save_as_path)

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
# Local-only equivalents -- used by LocalFinalizeWorker (ui/sync_workers.py)
# when Finalize runs with no sync partner configured. Same end results as
# the functions above -- new mail gets scanned in, a Finalize still exports
# and closes out the period locally -- just with no pull from, push to, or
# notification of another device, and therefore no partner email required
# at all.
# ----------------------------------------------------------------------

def local_update(project_type=None, progress_callback=None):
    """Sync-off equivalent of the 'Update' button: scans this device's
    own inbox (same as Scan Inbox), then tops up the active export sheet
    with whatever that scan brought in -- with no pull from or push to
    another device. The export half is the same rolling sheet the sync-on
    Update fills (see update_with_other_user), so turning sync off doesn't
    put the app on a different export file -- project_type included, or
    the History page's division toggle would do nothing with sync off."""
    sync_cards(progress_callback=progress_callback)

    if progress_callback:
        progress_callback("Updating the export file...")
    export_result = rebuild_active_export(project_type=project_type)

    return {"scanned": True, "export": export_result}


def local_finalize(start_date, end_date, project_type=None, progress_callback=None, save_as_path=None):
    """Sync-off equivalent of 'Finalize': scans this device's own inbox,
    then closes out the active export sheet locally -- no pull, no
    notification. As with finalize_month there is no output_path: the
    file closed is whichever one Update has been filling -- save_as_path
    only says where a COPY of the closed file should also land (see
    _copy_finalized_export; shown to the user and cancel-checked in the
    UI BEFORE this is ever called, same as finalize_month). notified is
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
    output_path = finalized["path"]

    if save_as_path:
        report("Saving the finalized export to the chosen location...")
        output_path = _copy_finalized_export(output_path, save_as_path)

    return {"row_count": finalized["row_count"], "notified": None, "path": output_path}
