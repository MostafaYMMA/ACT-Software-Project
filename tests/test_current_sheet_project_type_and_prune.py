"""
Two additions to Current Sheet:
  1. A project-type ("All"/Food & Beverage/Hospitality) filter on
     get_current_sheet_rows(), matching the same toggle convention used
     on Dashboard/Export History.
  2. prune_old_month_current_sheet_rows(): an explicit, on-demand cleanup
     that deletes current_sheet rows outside the current calendar month.
     NOT wired into sync_current_sheet (see that function's own docstring:
     "a re-scan can only ever ADD rows here") -- only ever run when
     explicitly called.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service


def _entry(status, day, project_number, project_name, task, person_number, received):
    return {
        "status": status, "day": day, "project_name": project_name,
        "project_code": project_number, "task": task, "hours": "8",
        "name": "Jane", "person_number": person_number,
        "subject": f"Time Entries {status}", "sender": "s@x.com",
        "received": received, "labor_type": "L", "time_type": "R",
        "period": "2026-06-29 to 2026-07-05",
        "rate": None, "rate_updated_at": None, "rate_updated_by": None,
    }


class CurrentSheetProjectTypeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        patcher = patch.object(storage_service, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        storage_service.init_db()

    def test_filters_to_food_and_beverage(self):
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "FB Kitchen", "T1", "P1", "2026-07-01 09:00:00"),
            _entry("Approved", "2026-07-02", "HL-200", "HL Hotel", "T2", "P2", "2026-07-02 09:00:00"),
        ])

        fb_rows = storage_service.get_current_sheet_rows(project_type="beverage")
        hl_rows = storage_service.get_current_sheet_rows(project_type="hospitality")
        all_rows = storage_service.get_current_sheet_rows()

        self.assertEqual(len(fb_rows), 1)
        self.assertEqual(fb_rows[0]["Project Name"], "FB Kitchen")
        self.assertEqual(len(hl_rows), 1)
        self.assertEqual(hl_rows[0]["Project Name"], "HL Hotel")
        self.assertEqual(len(all_rows), 2)

    def test_default_is_all_projects(self):
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "FB Kitchen", "T1", "P1", "2026-07-01 09:00:00"),
            _entry("Approved", "2026-07-02", "HL-200", "HL Hotel", "T2", "P2", "2026-07-02 09:00:00"),
        ])
        self.assertEqual(len(storage_service.get_current_sheet_rows(project_type=None)), 2)


class PruneOldMonthCurrentSheetRowsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        patcher = patch.object(storage_service, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        storage_service.init_db()

    def _row_count(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM current_sheet").fetchone()[0]
        finally:
            conn.close()

    def test_removes_rows_outside_the_current_month_only(self):
        storage_service.save_cards([
            _entry("Approved", "2026-05-01", "FB-100", "FB Old", "T1", "P1", "2026-05-01 09:00:00"),
            _entry("Approved", "2026-06-15", "FB-101", "FB Older", "T2", "P2", "2026-06-15 09:00:00"),
            _entry("Approved", "2026-07-01", "FB-102", "FB Current", "T3", "P3", "2026-07-01 09:00:00"),
        ])
        self.assertEqual(self._row_count(), 3)

        removed = storage_service.prune_old_month_current_sheet_rows(reference_date="2026-07-15")

        self.assertEqual(removed, 2)
        self.assertEqual(self._row_count(), 1)
        remaining = storage_service.get_current_sheet_rows()
        self.assertEqual(remaining[0]["Project Name"], "FB Current")

    def test_does_nothing_when_everything_is_already_current_month(self):
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "FB Current", "T1", "P1", "2026-07-01 09:00:00"),
        ])
        removed = storage_service.prune_old_month_current_sheet_rows(reference_date="2026-07-20")
        self.assertEqual(removed, 0)
        self.assertEqual(self._row_count(), 1)

    def test_is_not_called_automatically_by_a_normal_scan(self):
        """The whole point: an old-month row must survive an ordinary
        save_cards() call (what a real Scan Inbox does) untouched -- only
        an explicit prune call removes it."""
        storage_service.save_cards([
            _entry("Approved", "2026-05-01", "FB-100", "FB Old", "T1", "P1", "2026-05-01 09:00:00"),
        ])
        # Simulate another scan happening later, in a later month.
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-101", "FB New", "T2", "P2", "2026-07-01 09:00:00"),
        ])
        self.assertEqual(self._row_count(), 2, "an ordinary scan must never silently drop an old-month row")

    def test_only_deletes_current_sheet_not_timecards_approved_or_invoice_lines(self):
        storage_service.save_cards([
            _entry("Approved", "2026-05-01", "FB-100", "FB Old", "T1", "P1", "2026-05-01 09:00:00"),
        ])
        storage_service.prune_old_month_current_sheet_rows(reference_date="2026-07-15")

        conn = sqlite3.connect(self.db_path)
        try:
            approved_count = conn.execute("SELECT COUNT(*) FROM timecards_approved").fetchone()[0]
            invoice_lines_count = conn.execute("SELECT COUNT(*) FROM invoice_lines").fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(approved_count, 1, "timecards_approved must be untouched")
        self.assertEqual(invoice_lines_count, 1, "invoice_lines must be untouched")


if __name__ == "__main__":
    unittest.main()
