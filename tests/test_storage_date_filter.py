import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service


class StatusDateFilterTests(unittest.TestCase):
    """
    Covers the Dashboard's "filter the display by the selected period, not
    just what a scan pulls in" requirement: get_status_rows() and
    get_status_project_counts() should only surface rows whose "received"
    timestamp falls within [start_date, end_date] once a range is given,
    and behave exactly as before (no filtering) when it isn't.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "cards_test.db")

        self.db_path_patcher = patch.object(storage_service, "DB_PATH", self.db_path)
        self.db_path_patcher.start()
        self.addCleanup(self.db_path_patcher.stop)

        storage_service.init_db()
        self._insert_row("timecards_approved", day="Monday, 06 Jul", subject="A", received="2026-07-05 10:00:00")
        self._insert_row("timecards_approved", day="Tuesday, 07 Jul", subject="B", received="2026-06-20 10:00:00")
        self._insert_row("timecards_approved", day="Wednesday, 08 Jul", subject="C", received="2026-07-13 09:00:00")
        self._insert_row("timecards_pending", day="Monday, 06 Jul", subject="D", received="2026-07-10 10:00:00")

    def _insert_row(self, table, day, subject, received, project_number="P1", task="T1", sender="s@x.com"):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            f'INSERT INTO "{table}" '
            f'(day, "Date", subject, sender, "Project Number", "Task Name", received) '
            f'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (day, received[:10], subject, sender, project_number, task, received),
        )
        conn.commit()
        conn.close()

    def test_get_status_rows_no_range_returns_everything(self):
        rows = storage_service.get_status_rows("approve")
        self.assertEqual(sorted(r["subject"] for r in rows), ["A", "B", "C"])

    def test_get_status_rows_filters_by_received_within_range(self):
        rows = storage_service.get_status_rows(
            "approve", start_date=datetime(2026, 7, 1), end_date=datetime(2026, 7, 31, 23, 59, 59)
        )
        self.assertEqual(sorted(r["subject"] for r in rows), ["A", "C"])

    def test_get_status_project_counts_no_range_counts_everything(self):
        counts = storage_service.get_status_project_counts()
        self.assertEqual(counts["approve"], 3)
        self.assertEqual(counts["pending"], 1)

    def test_get_status_project_counts_filters_by_received_within_range(self):
        counts = storage_service.get_status_project_counts(
            start_date=datetime(2026, 7, 1), end_date=datetime(2026, 7, 31, 23, 59, 59)
        )
        self.assertEqual(counts["approve"], 2)
        self.assertEqual(counts["pending"], 1)

    def test_this_month_range_matches_dashboard_default(self):
        import date_utils

        start, end = date_utils.get_this_month_range(datetime(2026, 7, 13, 12, 0, 0))
        rows = storage_service.get_status_rows("approve", start_date=start, end_date=end)
        self.assertEqual(sorted(r["subject"] for r in rows), ["A", "C"])


if __name__ == "__main__":
    unittest.main()
