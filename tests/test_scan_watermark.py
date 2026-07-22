import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service


class ScanWatermarkTests(unittest.TestCase):
    """
    Covers the incremental-scan high-water mark: sync_cards starts each
    inbox scan from get_last_scan_time() (minus an overlap) instead of
    re-walking the whole folder, so the two accessors have to be exact to
    the second and must never move backwards.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "cards_test.db")

        self.db_path_patcher = patch.object(storage_service, "DB_PATH", self.db_path)
        self.db_path_patcher.start()
        self.addCleanup(self.db_path_patcher.stop)

        storage_service.init_db()

    def test_no_mark_before_the_first_scan(self):
        self.assertIsNone(storage_service.get_last_scan_time())

    def test_round_trips_to_the_second(self):
        received = datetime(2026, 7, 22, 14, 32, 7)
        storage_service.set_last_scan_time(received)
        self.assertEqual(storage_service.get_last_scan_time(), received)

    def test_moves_forward(self):
        storage_service.set_last_scan_time(datetime(2026, 7, 22, 9, 0, 0))
        storage_service.set_last_scan_time(datetime(2026, 7, 22, 14, 32, 7))
        self.assertEqual(
            storage_service.get_last_scan_time(), datetime(2026, 7, 22, 14, 32, 7)
        )

    def test_never_moves_backwards(self):
        """An older timestamp is ignored rather than stored -- rewinding the
        mark and then subtracting the scan overlap could otherwise open a
        window that skips mail entirely."""
        newest = datetime(2026, 7, 22, 14, 32, 7)
        storage_service.set_last_scan_time(newest)
        storage_service.set_last_scan_time(datetime(2026, 7, 20, 8, 0, 0))
        self.assertEqual(storage_service.get_last_scan_time(), newest)

    def test_none_leaves_the_mark_alone(self):
        """get_newest_received returns None when the folder is empty or
        Outlook can't be reached; that must not wipe the mark and force a
        full re-scan."""
        newest = datetime(2026, 7, 22, 14, 32, 7)
        storage_service.set_last_scan_time(newest)
        storage_service.set_last_scan_time(None)
        self.assertEqual(storage_service.get_last_scan_time(), newest)

    def test_tz_aware_input_is_stored_naive(self):
        """Outlook hands back a tz-aware pywintypes.datetime whose offset
        doesn't reflect a real conversion (see
        filter_service._received_time_naive) -- it has to land in the same
        naive shape everything else compares against."""
        from datetime import timedelta, timezone

        aware = datetime(2026, 7, 22, 14, 32, 7, tzinfo=timezone(timedelta(hours=4)))
        storage_service.set_last_scan_time(aware)
        stored = storage_service.get_last_scan_time()
        self.assertIsNone(stored.tzinfo)
        self.assertEqual(stored, datetime(2026, 7, 22, 14, 32, 7))


if __name__ == "__main__":
    unittest.main()
