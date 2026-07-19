"""
Accuracy tests for the period-window logic in
filter_service.get_approved_cards / get_expense_reports -- the same
[start_date, end_date] rules the Dashboard scan uses.

These pin down the exact boundary semantics (inclusive edges, tz
handling, whole-end-day rule) so the live cross-check against a real
mailbox (tests/verify_scan_vs_outlook.py) only has to worry about real
Outlook data, not the window math.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import date_utils
import filter_service


class _MailItem:
    def __init__(self, name, received_time, item_class=filter_service.OL_MAIL_ITEM_CLASS):
        self.Class = item_class
        self.name = name
        self.ReceivedTime = received_time


class _RaisingReceivedItem:
    """MailItem whose ReceivedTime property blows up, as a broken MAPI
    item can."""
    Class = filter_service.OL_MAIL_ITEM_CLASS
    name = "raising"

    @property
    def ReceivedTime(self):
        raise OSError("MAPI property unavailable")


class _FakeItems(list):
    def Sort(self, *args, **kwargs):
        pass  # test data is already provided newest-first


class _ScanHarness(unittest.TestCase):
    """Runs get_approved_cards / get_expense_reports over fake items with
    the per-email processing mocked out, and returns the names of the
    items the scan actually handed to processing -- i.e. exactly which
    emails the period logic let through."""

    def _processed_names(self, mail_items, expense=False, **kwargs):
        fake_folder = MagicMock()
        fake_folder.Items = _FakeItems(mail_items)

        fake_outlook = MagicMock()
        fake_outlook.GetNamespace.return_value = MagicMock()

        process_name = "process_email_expense" if expense else "process_email"
        entry_point = (filter_service.get_expense_reports if expense
                       else filter_service.get_approved_cards)

        with patch.object(filter_service.win32com.client, "Dispatch", return_value=fake_outlook), \
             patch.object(filter_service, "get_outlook_folder", return_value=fake_folder), \
             patch.object(filter_service, process_name) as mock_process:
            mock_process.return_value = None
            entry_point(print_report=False, **kwargs)

        return [call.args[0].name for call in mock_process.call_args_list]


class BoundaryInclusivityTests(_ScanHarness):
    START = datetime(2026, 7, 1, 0, 0, 0)
    END = datetime(2026, 7, 15, 23, 59, 59, 999999)

    def test_edges_are_inclusive_to_the_microsecond(self):
        items = [
            _MailItem("after_end", self.END + timedelta(microseconds=1)),
            _MailItem("exactly_end", self.END),
            _MailItem("middle", datetime(2026, 7, 8, 12, 0, 0)),
            _MailItem("exactly_start", self.START),
            _MailItem("before_start", self.START - timedelta(microseconds=1)),
        ]
        processed = self._processed_names(items, start_date=self.START, end_date=self.END)
        self.assertEqual(processed, ["exactly_end", "middle", "exactly_start"])

    def test_expense_scan_applies_the_identical_window(self):
        items = [
            _MailItem("after_end", self.END + timedelta(microseconds=1)),
            _MailItem("exactly_end", self.END),
            _MailItem("middle", datetime(2026, 7, 8, 12, 0, 0)),
            _MailItem("exactly_start", self.START),
            _MailItem("before_start", self.START - timedelta(microseconds=1)),
        ]
        processed = self._processed_names(items, expense=True,
                                          start_date=self.START, end_date=self.END)
        self.assertEqual(processed, ["exactly_end", "middle", "exactly_start"])


class DashboardCustomRangeTests(_ScanHarness):
    """End-to-end with date_utils.get_custom_range, exactly as the
    Dashboard builds a custom From/To period (date-only pickers)."""

    def test_whole_end_day_is_included_and_neighbors_are_not(self):
        start, end = date_utils.get_custom_range(datetime(2026, 7, 1), datetime(2026, 7, 5))
        items = [
            _MailItem("next_day_midnight", datetime(2026, 7, 6, 0, 0, 0)),
            _MailItem("end_day_evening", datetime(2026, 7, 5, 23, 59, 59)),
            _MailItem("start_day_midnight", datetime(2026, 7, 1, 0, 0, 0)),
            _MailItem("prev_day_evening", datetime(2026, 6, 30, 23, 59, 59, 999999)),
        ]
        processed = self._processed_names(items, start_date=start, end_date=end)
        self.assertEqual(processed, ["end_day_evening", "start_day_midnight"])

    def test_single_day_range_covers_that_full_day(self):
        start, end = date_utils.get_custom_range(datetime(2026, 7, 10), datetime(2026, 7, 10))
        items = [
            _MailItem("day_after", datetime(2026, 7, 11, 0, 0, 0)),
            _MailItem("late_that_day", datetime(2026, 7, 10, 23, 30, 0)),
            _MailItem("early_that_day", datetime(2026, 7, 10, 0, 0, 0)),
            _MailItem("day_before", datetime(2026, 7, 9, 23, 59, 59)),
        ]
        processed = self._processed_names(items, start_date=start, end_date=end)
        self.assertEqual(processed, ["late_that_day", "early_that_day"])


class TimezoneHandlingTests(_ScanHarness):
    """Outlook/pywin32 hands back ReceivedTime tz-aware, but its tzinfo is
    a label on local wall-clock time, not a real conversion --
    _received_time_naive() must compare by wall clock (drop tzinfo), never
    convert."""

    def test_tz_aware_received_times_compared_as_wall_clock(self):
        tz = timezone(timedelta(hours=3))  # arbitrary offset label
        start = datetime(2026, 7, 1)
        end = datetime(2026, 7, 15, 23, 59, 59, 999999)
        items = [
            _MailItem("aware_in_range", datetime(2026, 7, 10, 12, 0, 0, tzinfo=tz)),
            # Wall clock 2026-06-30 23:00 is before start; a UTC conversion
            # (+3h offset -> 20:00 UTC... or interpreted differently) could
            # flip the comparison. Must break here regardless of tzinfo.
            _MailItem("aware_too_old", datetime(2026, 6, 30, 23, 0, 0, tzinfo=tz)),
            _MailItem("never_reached", datetime(2026, 6, 1, tzinfo=tz)),
        ]
        processed = self._processed_names(items, start_date=start, end_date=end)
        self.assertEqual(processed, ["aware_in_range"])


class RobustnessTests(_ScanHarness):
    START = datetime(2026, 7, 1)
    END = datetime(2026, 7, 15, 23, 59, 59, 999999)

    def test_unreadable_received_time_skips_that_item_only(self):
        items = [
            _MailItem("in_range_1", datetime(2026, 7, 10)),
            _RaisingReceivedItem(),
            _MailItem("in_range_2", datetime(2026, 7, 5)),
        ]
        processed = self._processed_names(items, start_date=self.START, end_date=self.END)
        self.assertEqual(processed, ["in_range_1", "in_range_2"])

    def test_non_mail_items_are_ignored_without_ending_the_scan(self):
        items = [
            _MailItem("meeting_request", datetime(2026, 7, 12), item_class=53),
            _MailItem("in_range", datetime(2026, 7, 10)),
            _MailItem("report_item", datetime(2026, 7, 8), item_class=46),
            _MailItem("also_in_range", datetime(2026, 7, 5)),
        ]
        processed = self._processed_names(items, start_date=self.START, end_date=self.END)
        self.assertEqual(processed, ["in_range", "also_in_range"])


if __name__ == "__main__":
    unittest.main()
