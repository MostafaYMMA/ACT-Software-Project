"""
Sends and reads the cross-device SYNC mail used to keep two users' local
databases merged with no shared server (see services/sync_service.py for
the orchestration that calls these, and services/storage_service.py for
what the payloads actually mean).

Deliberately separate from filter_service.py: that module's job is
finding REAL approved/pending/rejected timecard emails from clients; this
module's job is a private channel between the two ACT app installs
themselves. Keeping them apart means a change to one's matching logic can
never accidentally start matching (or ignoring) the other's mail.
"""

import os
import re
import shutil
import tempfile
import traceback

import win32com.client

from sync_payload_excel import (
    write_payload_workbook, read_payload_workbook,
    write_sheet_payload_files, read_sheet_payload_json,
    SHEET_JSON_ATTACHMENT_NAME, SHEET_XLSX_ATTACHMENT_NAME,
)

OL_FOLDER_INBOX = 6
OL_MAIL_ITEM_CLASS = 43

# Every sync mail's subject looks like:
#   ACT-SYNC v1 | snapshot | a1b2c3d4e5f6 | seq=7
# - kind: "snapshot" (a device's current-period timecard data),
#   "rate" (one standalone rate edit), "finalize" (month-close notice), or
#   "sheet" (the current_sheet rows edited since this device's last push --
#   see storage_service.build_current_sheet_sync_payload; the only kind
#   whose real data is a JSON attachment rather than the .xlsx).
# - device_id / seq: see storage_service.get_device_id / _next_outgoing_seq.
# This exact, distinctive shape is what keeps filter_service's approval
# regex from ever mistaking one of these for a real timecard email (it
# requires "time card"/"FW: FYI" in the subject, which this never
# contains) and what lets scan_sync_mails() below find them again.
_SUBJECT_PREFIX = "ACT-SYNC"
_SUBJECT_PATTERN = re.compile(
    r"^ACT-SYNC v1 \| (snapshot|rate|finalize|sheet) \| ([a-f0-9]+) \| seq=(\d+)",
    re.IGNORECASE,
)
_PAYLOAD_ATTACHMENT_NAME = "act_sync_payload.xlsx"


def _build_subject(kind, device_id, seq):
    return f"{_SUBJECT_PREFIX} v1 | {kind} | {device_id} | seq={seq}"


def send_sync_mail(recipient_email, kind, payload, seq, extra_attachments=None, note=""):
    """
    Sends one sync message via Outlook. `payload` is rendered to a real
    .xlsx (see sync_payload_excel.write_payload_workbook) and attached as
    a file (not put in the mail body), so it round-trips exactly and
    opens as readable timecard rows if anyone looks at it directly.
    extra_attachments (a list of real file paths) rides along for a
    "finalize" message, which also attaches the actual exported .xlsx so
    the other user's app can save/register it.

    Returns True if the mail was handed to Outlook to send, False on any
    failure (Outlook not running, no default profile, etc) -- the caller
    is expected to tell the user the update went out locally even if the
    notification failed, rather than silently losing the local export.
    """
    device_id = payload.get("device_id", "")
    subject = _build_subject(kind, device_id, seq)

    temp_dir = tempfile.mkdtemp(prefix="act_sync_send_")
    try:
        if kind == "sheet":
            # Two attachments: the JSON the other app actually reads, and a
            # formatted .xlsx of the same rows purely so a human opening
            # the mail sees the changes instead of raw JSON.
            attachment_paths = [
                os.path.join(temp_dir, SHEET_JSON_ATTACHMENT_NAME),
                os.path.join(temp_dir, SHEET_XLSX_ATTACHMENT_NAME),
            ]
            write_sheet_payload_files(payload, attachment_paths[0], attachment_paths[1])
        else:
            attachment_paths = [os.path.join(temp_dir, _PAYLOAD_ATTACHMENT_NAME)]
            write_payload_workbook(payload, kind, attachment_paths[0])

        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)  # olMailItem
        mail.To = recipient_email
        mail.Subject = subject
        mail.Body = (
            "This is an automated sync message from the ACT timecard app.\n"
            "Please do not reply to or delete it manually -- it's handled by the app.\n\n"
            + (note or "")
        )
        for attachment_path in attachment_paths:
            mail.Attachments.Add(attachment_path)
        for extra_path in (extra_attachments or []):
            if extra_path and os.path.exists(extra_path):
                mail.Attachments.Add(extra_path)
        mail.Send()
        return True
    except Exception:
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _extract_attachments(item, temp_dir, kind):
    """Saves every attachment on `item` to temp_dir and reads the payload
    .xlsx out of them (see sync_payload_excel.read_payload_workbook).
    `kind` is the one already parsed off the subject (see
    _SUBJECT_PATTERN) -- needed to know which payload shape to
    reconstruct. Returns (payload_dict_or_None, [other saved file
    paths])."""
    payload = None
    other_paths = []
    try:
        attachments = item.Attachments
        for i in range(1, attachments.Count + 1):
            attachment = attachments.Item(i)
            filename = attachment.FileName
            saved_path = os.path.join(temp_dir, f"{i}_{filename}")
            attachment.SaveAsFile(saved_path)
            if kind == "sheet":
                # ONLY the JSON is ever parsed. The .xlsx riding alongside
                # it is a human-readable copy and is deliberately left
                # alone -- treating it as data would mean two sources of
                # truth for the same rows.
                if filename == SHEET_JSON_ATTACHMENT_NAME:
                    payload = read_sheet_payload_json(saved_path)
            elif filename == _PAYLOAD_ATTACHMENT_NAME:
                payload = read_payload_workbook(saved_path, kind)
            else:
                other_paths.append(saved_path)
    except Exception:
        traceback.print_exc()
    return payload, other_paths


def scan_sync_mails(folder_name="Inbox", limit=200):
    """
    Finds every UNREAD sync mail (see _SUBJECT_PATTERN), reads its .xlsx
    payload and any extra attachments, and marks it read once handled.

    Only UNREAD mail is scanned. Correctness doesn't depend on this --
    every kind of payload is idempotent on the receiving end (see
    storage_service.apply_incoming_snapshot / apply_rate_update /
    record_finalize_from_other_device) -- it's purely so this doesn't
    re-download months of old sync attachments on every single check.

    Returns (messages, temp_dir):
      messages: list of {kind, device_id, seq, payload, extra_paths,
                subject}, oldest first.
      temp_dir: where extra_paths (e.g. a finalize's exported .xlsx) were
                saved -- the CALLER is responsible for deleting this once
                it's done with any extra_paths it needed (see
                sync_service.pull_updates), since a finalize's attached
                file needs to still exist after this function returns.
    """
    messages = []
    temp_dir = tempfile.mkdtemp(prefix="act_sync_scan_")

    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")
        inbox = namespace.GetDefaultFolder(OL_FOLDER_INBOX)
        folder = inbox
        if folder_name.lower() != "inbox":
            for sub in inbox.Folders:
                if sub.Name.lower() == folder_name.lower():
                    folder = sub
                    break
    except Exception:
        traceback.print_exc()
        return messages, temp_dir

    items = folder.Items
    items.Sort("[ReceivedTime]", False)  # oldest first, so callers naturally settle on newest per sender

    scanned = 0
    try:
        for item in items:
            if scanned >= limit:
                break
            try:
                if getattr(item, "Class", None) != OL_MAIL_ITEM_CLASS:
                    continue
                if not getattr(item, "UnRead", False):
                    continue

                subject = getattr(item, "Subject", "") or ""
                match = _SUBJECT_PATTERN.match(subject)
                if not match:
                    continue

                scanned += 1
                kind, device_id, seq = match.group(1), match.group(2), int(match.group(3))
                payload, extra_paths = _extract_attachments(item, temp_dir, kind)
                if payload is None:
                    continue

                messages.append({
                    "kind": kind, "device_id": device_id, "seq": seq,
                    "payload": payload, "extra_paths": extra_paths,
                    "subject": subject,
                })
                item.UnRead = False
            except Exception:
                traceback.print_exc()
                continue
    except Exception:
        traceback.print_exc()

    return messages, temp_dir
