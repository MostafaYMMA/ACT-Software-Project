import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import date_utils
import filter_service


class GetThisMonthRangeTests(unittest.TestCase):
    def test_returns_day_one_through_now(self):
        now = datetime(2026, 7, 13, 15, 30, 45)
        start, end = date_utils.get_this_month_range(now)
        self.assertEqual(start, datetime(2026, 7, 1, 0, 0, 0))
        self.assertEqual(end, now)


class GetCustomRangeTests(unittest.TestCase):
    def test_normalizes_midnight_end_date_to_end_of_day(self):
        start = datetime(2026, 7, 1)
        end = datetime(2026, 7, 5)  # date-only picker value -> midnight
        norm_start, norm_end = date_utils.get_custom_range(start, end)
        self.assertEqual(norm_start, start)
        self.assertEqual(norm_end, datetime(2026, 7, 5, 23, 59, 59, 999999))

    def test_leaves_explicit_time_of_day_alone(self):
        start = datetime(2026, 7, 1)
        end = datetime(2026, 7, 5, 9, 0, 0)
        _, norm_end = date_utils.get_custom_range(start, end)
        self.assertEqual(norm_end, end)

    def test_rejects_start_after_end(self):
        with self.assertRaises(ValueError):
            date_utils.get_custom_range(datetime(2026, 7, 5), datetime(2026, 7, 1))


class _CountingMailItem:
    """Fake Outlook MailItem that records every ReceivedTime access, so
    tests can prove get_approved_cards() stopped scanning as soon as it
    passed an item older than start_date instead of walking the rest of
    the (newest-first sorted) folder."""

    def __init__(self, name, received_time, access_log):
        self.Class = filter_service.OL_MAIL_ITEM_CLASS
        self.name = name
        self._received_time = received_time
        self._access_log = access_log

    @property
    def ReceivedTime(self):
        self._access_log.append(self.name)
        return self._received_time


class _FakeItems(list):
    def Sort(self, *args, **kwargs):
        pass  # test data is already provided newest-first


class GetApprovedCardsDateRangeTests(unittest.TestCase):
    def _run_scan(self, mail_items, **kwargs):
        fake_folder = MagicMock()
        fake_folder.Items = _FakeItems(mail_items)

        fake_outlook = MagicMock()
        fake_outlook.GetNamespace.return_value = MagicMock()

        with patch.object(filter_service.win32com.client, "Dispatch", return_value=fake_outlook), \
             patch.object(filter_service, "get_outlook_folder", return_value=fake_folder), \
             patch.object(filter_service, "process_email") as mock_process_email:
            mock_process_email.return_value = None
            filter_service.get_approved_cards(print_report=False, **kwargs)
        return mock_process_email

    def test_skips_newer_and_stops_at_older_without_scanning_the_rest(self):
        access_log = []
        # Newest-first, as Outlook's own sort would produce.
        items = [
            _CountingMailItem("too_new", datetime(2026, 7, 20), access_log),
            _CountingMailItem("in_range_1", datetime(2026, 7, 10), access_log),
            _CountingMailItem("in_range_2", datetime(2026, 7, 5), access_log),
            _CountingMailItem("too_old", datetime(2026, 6, 20), access_log),
            _CountingMailItem("never_touched", datetime(2026, 6, 1), access_log),
        ]

        mock_process_email = self._run_scan(
            items,
            start_date=datetime(2026, 7, 1),
            end_date=datetime(2026, 7, 15),
        )

        processed_names = [call.args[0].name for call in mock_process_email.call_args_list]
        self.assertEqual(processed_names, ["in_range_1", "in_range_2"])

        # The scan must stop the instant it sees an item older than
        # start_date -- "never_touched"'s ReceivedTime should never even
        # be read.
        self.assertEqual(access_log, ["too_new", "in_range_1", "in_range_2", "too_old"])

    def test_this_month_range_end_to_end_with_date_utils(self):
        access_log = []
        now = datetime(2026, 7, 13, 12, 0, 0)
        start, end = date_utils.get_this_month_range(now)
        items = [
            _CountingMailItem("this_month", datetime(2026, 7, 13, 8, 0, 0), access_log),
            _CountingMailItem("last_month", datetime(2026, 6, 30, 23, 59, 59), access_log),
        ]

        mock_process_email = self._run_scan(items, start_date=start, end_date=end)

        processed_names = [call.args[0].name for call in mock_process_email.call_args_list]
        self.assertEqual(processed_names, ["this_month"])

    def test_no_date_range_processes_everything_unchanged(self):
        access_log = []
        items = [
            _CountingMailItem("a", datetime(2026, 7, 20), access_log),
            _CountingMailItem("b", datetime(2020, 1, 1), access_log),
        ]

        mock_process_email = self._run_scan(items)

        self.assertEqual(mock_process_email.call_count, 2)


if __name__ == "__main__":
    unittest.main()
