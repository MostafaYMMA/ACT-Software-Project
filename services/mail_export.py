"""
Saves a raw Outlook MailItem to disk as a native .msg file, so it can be
reopened later straight from File Explorer (double-click launches it in
Outlook, with attachments/headers/body intact).

Also home to the Timecards_emails/Expenses_emails archive: every APPROVED
timecard/expense email filter_service matches gets a copy saved here,
organized by received month then sender address, before sync_service ever
extracts fields out of it (see sync_service.sync_cards).
"""

import os
import re

_OLMSG = 3  # Outlook's olMSG SaveAs format constant

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMECARDS_ARCHIVE_DIR = os.path.join(BASE_DIR, "Timecards_emails")
EXPENSES_ARCHIVE_DIR = os.path.join(BASE_DIR, "Expenses_emails")


def save_mail_as_msg(item, dest_path):
    """Saves `item` (a win32com Outlook MailItem, e.g. filter_service's
    `mail_item` field) to `dest_path` (should end in .msg). Returns
    dest_path on success."""
    item.SaveAs(dest_path, _OLMSG)
    return dest_path


def safe_filename(subject, received=None, suffix=".msg"):
    """Turns an email subject (and optional received datetime) into a
    filesystem-safe filename, since subjects can contain characters
    Windows rejects in paths."""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", subject or "email").strip()
    if received is not None:
        cleaned = f"{received:%Y-%m-%d}_{cleaned}"
    return cleaned[:150] + suffix


def resolve_sender_email(item):
    """Best-effort SMTP address for `item`'s sender, for use as a folder
    name. item.SenderEmailAddress returns an Exchange legacyExchangeDN
    (starts with '/O=') instead of a real address for on-prem/hybrid
    Exchange accounts -- GetExchangeUser().PrimarySmtpAddress resolves
    that case. Falls back to the sanitized display name if neither is
    available (e.g. sender left the org, GAL lookup fails)."""
    try:
        address = item.SenderEmailAddress or ""
    except Exception:
        address = ""

    if address.startswith("/O="):
        try:
            exchange_user = item.Sender.GetExchangeUser()
            if exchange_user is not None:
                smtp = exchange_user.PrimarySmtpAddress
                if smtp:
                    address = smtp
        except Exception:
            pass

    if not address or address.startswith("/O="):
        try:
            address = item.SenderName or "unknown_sender"
        except Exception:
            address = "unknown_sender"

    return _INVALID_FILENAME_CHARS.sub("_", address).strip()


def archive_matched_email(item, received, base_dir, prefix):
    """Saves `item` into base_dir/<YYYY-MM>/<sender_email>/<prefix>_<received
    down to the second>.msg, creating the month/sender folders as needed.

    Including seconds in the filename means a re-scan of the same email
    (SCAN_OVERLAP re-walks a few minutes of already-seen mail) reproduces
    the exact same path -- so an existing file is treated as "already
    archived" and skipped, and two different emails from the same sender
    in the same minute don't collide.

    Returns the path saved to, or None if it was already archived or
    `received` wasn't a real datetime (item.ReceivedTime failed to read --
    see filter_service.process_email/process_email_expense's "(unknown
    date)" fallback).
    """
    try:
        month_key = f"{received:%Y-%m}"
        timestamp_key = f"{received:%Y-%m-%d_%H-%M-%S}"
    except (TypeError, ValueError):
        return None

    month_dir = os.path.join(base_dir, month_key)
    sender_dir = os.path.join(month_dir, resolve_sender_email(item))
    os.makedirs(sender_dir, exist_ok=True)

    filename = f"{prefix}_{timestamp_key}.msg"
    dest_path = os.path.join(sender_dir, filename)

    if os.path.exists(dest_path):
        return None

    return save_mail_as_msg(item, dest_path)
