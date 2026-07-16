import os
import shutil

from filter_service import get_approved_cards
from extractor_service import extract
from storage_service import (
    init_db, save_cards, export_to_csv, export_invoice_lines_to_excel,
    build_outgoing_snapshot, apply_incoming_snapshot, apply_rate_update,
    build_rate_update_payload, get_device_id, export_act_invoice_overview_range,
    record_finalize_from_other_device,
)
from outlook_service import send_sync_mail, scan_sync_mails


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


# ----------------------------------------------------------------------
# Two-device sync: pulling in what the other user sent, and pushing out
# what this device has scanned. See storage_service.py for why each of
# these is safe to call repeatedly / out of order / with stale mail still
# sitting in the inbox.
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
                # sync_service.finalize_month) -- copied next to this
                # device's own exports so it's not lost when temp_dir is
                # cleaned up below, and so the user can find it without
                # having to dig through Outlook attachments.
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
    Scans this device's own inbox for new approved/pending/rejected
    timecards (same as a normal Scan Inbox), then mails the other user a
    full current-period snapshot of what THIS device has scanned.
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    sync_cards(progress_callback=progress_callback)

    report("Preparing update for the other user...")
    snapshot = build_outgoing_snapshot(project_type=project_type)
    if not snapshot["rows"]:
        report("Nothing new to send.")
        return {"sent": False, "reason": "nothing to send"}

    sent = send_sync_mail(recipient_email, "snapshot", snapshot, snapshot["seq"])
    report("Update sent." if sent else "Failed to send the update email.")
    return {"sent": sent, "rows_sent": len(snapshot["rows"])}


def update_with_other_user(recipient_email, project_type=None, progress_callback=None):
    """The 'Update' button on the Export History page: pulls in anything
    waiting from the other user, then pushes this device's own new data
    out to them. Pull-before-push so this device's own snapshot (built
    during push) reflects anything just received, not a picture that's
    already one step behind."""
    pull_result = pull_updates(progress_callback=progress_callback)
    push_result = push_updates(recipient_email, project_type=project_type, progress_callback=progress_callback)
    return {"pull": pull_result, "push": push_result}


def push_rate_update(status_key, record_id, recipient_email):
    """
    Sends ONE rate edit immediately, independent of the full snapshot
    Update/Finalize flow. This is what actually closes the gap a plain
    snapshot can't: build_outgoing_snapshot only ever includes rows this
    device scanned itself (see storage_service.save_cards), so an edit
    to a rate on a record the OTHER device originally scanned would
    never go out through Update at all, on either side, ever -- it needs
    its own small standalone message instead. See ui/Pages/Dashboard.py's
    _on_item_changed, which calls this right after a successful rate edit.
    """
    payload = build_rate_update_payload(status_key, record_id)
    if payload is None:
        return False
    # seq is only meaningful for "snapshot" messages (see
    # apply_incoming_snapshot's supersede check) -- a "rate" message is
    # applied purely by comparing rate_updated_at, so seq here only needs
    # to satisfy the subject pattern, not mean anything on its own.
    return send_sync_mail(recipient_email, "rate", payload, seq=0)


def finalize_month(recipient_email, start_date, end_date, output_path, project_type=None, progress_callback=None):
    """
    The 'Finalize' button, called AFTER the user has confirmed in the UI.
    Order matters here:
      1. One last pull+push pass, so nothing sent moments ago on either
         side is missed right before the boundary moves.
      2. Export the real file. This also advances get_last_export_date
         and logs export_history LOCALLY (see export_act_invoice_overview_range) --
         that part already existed before this feature.
      3. Mail the other user a "finalize" notice: the exported file
         itself, plus a closing snapshot so their data is fully caught
         up too. Their app applies this specially (see pull_updates
         above) -- logged into THEIR export_history and THEIR
         last_export_date is advanced too, so the boundary is shared
         rather than tracked per-machine.
    """

    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    update_with_other_user(recipient_email, project_type=project_type, progress_callback=progress_callback)

    report("Exporting final sheet...")
    row_count = export_act_invoice_overview_range(start_date, end_date, output_path, project_type=project_type)

    report("Notifying the other user...")
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
    report("Finalize notice sent." if sent else "Failed to notify the other user - the export still completed locally.")

    return {"row_count": row_count, "notified": sent}