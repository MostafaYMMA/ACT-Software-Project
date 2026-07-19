"""
Live accuracy cross-check for the period scan, against the REAL inbox.

Automates the manual verification step: "run the app's scan for a
period, then open Outlook, filter the inbox to the same dates, and
confirm the scan considered exactly those emails."

Ground truth comes from Outlook's own Restrict() date filter -- the same
engine a manual date-filtered search in the Outlook UI uses -- fetched
with a deliberately widened window (+/- 1 day, because Restrict is only
minute-granular) and then re-filtered in Python with the exact inclusive
[start, end] comparison the scan is supposed to implement. The scan side
is filter_service.get_approved_cards() (or get_expense_reports with
--expense) with its per-email processor instrumented so every email the
scan VISITS is recorded by EntryID, whether or not it matched.

The two sets are then diffed:

  MISSED = in the period per Outlook, but never visited by the scan
           (period-logic false negatives -- the bug a manual check
           would be hunting for; e.g. the newest-first early-stop
           breaking too soon)
  EXTRA  = visited by the scan, but outside the period per Outlook
           (false positives)

Usage (run from the repo root):
  python tests/verify_scan_vs_outlook.py                        # this month
  python tests/verify_scan_vs_outlook.py 2026-06-01 2026-06-30  # custom period
  python tests/verify_scan_vs_outlook.py 2026-06-01 2026-06-30 --full
  python tests/verify_scan_vs_outlook.py --expense              # expense filter

By default only the period traversal is checked (--full omitted): the
per-email matching (attachment download + text extraction) is skipped,
so even a large window verifies in seconds. With --full the real
matching also runs, and every MATCHED email is listed (subject / sender
/ received / matched-via) as a checklist to compare line-by-line with
what you see in Outlook.
"""
import argparse
import contextlib
import io
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import win32com.client

import date_utils
import filter_service
from filter_service import _received_time_naive

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Outlook Restrict date literals are minute-granular; the widened window
# plus the exact Python re-filter below is what makes boundaries precise.
RESTRICT_FMT = "%m/%d/%Y %I:%M %p"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("start", nargs="?", help="period start, YYYY-MM-DD (default: this month)")
    parser.add_argument("end", nargs="?", help="period end, YYYY-MM-DD (inclusive whole day)")
    parser.add_argument("--folder", default="Inbox", help="folder under Inbox to scan (default: Inbox)")
    parser.add_argument("--expense", action="store_true",
                        help="cross-check get_expense_reports instead of get_approved_cards")
    parser.add_argument("--full", action="store_true",
                        help="also run the real matching (attachments etc.) and list matched emails")
    args = parser.parse_args()

    if args.start and args.end:
        start, end = date_utils.get_custom_range(
            datetime.strptime(args.start, "%Y-%m-%d"),
            datetime.strptime(args.end, "%Y-%m-%d"),
        )
    elif args.start or args.end:
        parser.error("give both start and end, or neither")
    else:
        start, end = date_utils.get_this_month_range()
    return args, start, end


def outlook_ground_truth(folder_name, start, end):
    """Every mail item Outlook itself says was received in [start, end]:
    {EntryID: (received, subject)}."""
    outlook = win32com.client.Dispatch("Outlook.Application")
    namespace = outlook.GetNamespace("MAPI")
    folder = filter_service.get_outlook_folder(namespace, folder_name)

    items = folder.Items
    lo = (start - timedelta(days=1)).strftime(RESTRICT_FMT)
    hi = (end + timedelta(days=1)).strftime(RESTRICT_FMT)
    restricted = items.Restrict(f"[ReceivedTime] >= '{lo}' AND [ReceivedTime] <= '{hi}'")

    truth = {}
    for item in restricted:
        try:
            if getattr(item, "Class", None) != filter_service.OL_MAIL_ITEM_CLASS:
                continue
            received = _received_time_naive(item)
            if received is None or not (start <= received <= end):
                continue
            truth[item.EntryID] = (received, getattr(item, "Subject", "") or "")
        except Exception as exc:
            print(f"  (ground truth: skipped one unreadable item: {exc})")
    return truth


def instrumented_scan(folder_name, start, end, expense, full):
    """Runs the app's real period scan, recording every email it visits.

    Returns (visited, matched, captured_output):
      visited: {EntryID: (received, subject)} for every email the scan
               handed to its per-email processor
      matched: the scan's normal return value ([] in traversal-only mode,
               where the processor is stubbed out)
    """
    visited = {}
    process_name = "process_email_expense" if expense else "process_email"
    real_process = getattr(filter_service, process_name)
    entry_point = (filter_service.get_expense_reports if expense
                   else filter_service.get_approved_cards)

    def record(item):
        visited[item.EntryID] = (
            _received_time_naive(item),
            getattr(item, "Subject", "") or "",
        )

    def wrapper(item, *args, **kwargs):
        record(item)
        if not full:
            return None
        return real_process(item, *args, **kwargs)

    old_debug = filter_service.DEBUG
    filter_service.DEBUG = False
    setattr(filter_service, process_name, wrapper)
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            matched = entry_point(folder_name=folder_name, print_report=False,
                                  start_date=start, end_date=end)
    finally:
        setattr(filter_service, process_name, real_process)
        filter_service.DEBUG = old_debug
    return visited, matched, buffer.getvalue()


def report(truth, visited, matched, start, end, expense, full):
    scan_name = "get_expense_reports" if expense else "get_approved_cards"
    print("=" * 72)
    print(f"PERIOD SCAN CROSS-CHECK ({scan_name})")
    print(f"Period: {start}  ->  {end}  (inclusive)")
    print("=" * 72)
    print(f"Outlook says in period:   {len(truth)} emails (Restrict + exact re-filter)")
    print(f"Scan visited:             {len(visited)} emails")

    if visited:
        received_times = [r for r, _ in visited.values() if r is not None]
        if received_times:
            print(f"Scan visit range:         {min(received_times)}  ->  {max(received_times)}")

    missed = {eid: truth[eid] for eid in truth if eid not in visited}
    extra = {eid: visited[eid] for eid in visited if eid not in truth}

    print(f"\nMISSED (in period per Outlook, never visited by scan): {len(missed)}")
    for received, subject in sorted(missed.values()):
        print(f"    - {received}  |  {subject}")
    print(f"EXTRA (visited by scan, outside period per Outlook):   {len(extra)}")
    for received, subject in sorted(extra.values(), key=lambda x: (x[0] is None, x[0])):
        print(f"    - {received}  |  {subject}")

    if full:
        print(f"\nMATCHED by the real filter logic: {len(matched)} emails")
        print("(compare this checklist against a manual look in Outlook)")
        for m in matched:
            via = " + ".join(m.get("matched_via", [])) if not expense else \
                  ", ".join(m.get("terms_found", []))
            print(f"    - {m.get('received')}  |  {m.get('sender')}  |  {m.get('subject')}")
            print(f"          via: {via}")
    else:
        print("\n(traversal-only run: matching skipped; re-run with --full to also")
        print(" list which in-period emails the filter logic matches)")

    print("\n" + ("PASS: scan visited exactly the emails Outlook reports in the period."
                  if not missed and not extra else
                  "FAIL: discrepancies found -- see MISSED/EXTRA above."))
    return not missed and not extra


def main():
    args, start, end = parse_args()
    print(f"Building Outlook ground truth for {start} -> {end} ...")
    truth = outlook_ground_truth(args.folder, start, end)
    print(f"Running instrumented app scan ({'full' if args.full else 'traversal-only'}) ...")
    visited, matched, _ = instrumented_scan(args.folder, start, end, args.expense, args.full)
    ok = report(truth, visited, matched, start, end, args.expense, args.full)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
