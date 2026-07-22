import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service


class ActiveExportWriteFailureTests(unittest.TestCase):
    """
    A locked export sheet (open in Excel) must fail cleanly rather than
    half-committing: rebuild_active_export writes the workbook BEFORE its
    commit, so a failed write has to leave every row still counted as new
    for the next Update. Otherwise closing Excel and clicking Update again
    would produce a sheet missing exactly the rows that failed.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "cards_test.db")
        self.exports_dir = os.path.join(self.temp_dir.name, "exports")

        for name, value in (("DB_PATH", self.db_path), ("EXPORTS_DIR", self.exports_dir)):
            patcher = patch.object(storage_service, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        storage_service.init_db()
        self._insert_approved("Monday, 06 Jul", "A")
        self._insert_approved("Tuesday, 07 Jul", "B")

    def _insert_approved(self, day, subject):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'INSERT INTO timecards_approved '
            '(day, "Date", subject, sender, "Project Number", "Project Name", '
            '"Task Name", "Qty", rate, period, received) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (day, "2026-07-06", subject, "s@x.com", "P1", "FB Project",
             "T1", "8", "100", "from 2026-07-06 to 2026-07-12", "2026-07-06 10:00:00"),
        )
        conn.commit()
        conn.close()

    def _active_export_row_count(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM active_export_rows").fetchone()[0]
        finally:
            conn.close()

    def test_locked_file_leaves_no_rows_marked_as_exported(self):
        with patch.object(
            storage_service, "_write_act_invoice_workbook",
            side_effect=storage_service.ExportFileInUseError("locked"),
        ):
            with self.assertRaises(storage_service.ExportFileInUseError):
                storage_service.rebuild_active_export()

        self.assertEqual(self._active_export_row_count(), 0)

    def test_retry_after_the_lock_clears_writes_every_row(self):
        """The whole point of not committing on failure: closing Excel and
        clicking Update again has to produce the complete sheet, not one
        missing the rows from the failed attempt."""
        with patch.object(
            storage_service, "_write_act_invoice_workbook",
            side_effect=storage_service.ExportFileInUseError("locked"),
        ):
            with self.assertRaises(storage_service.ExportFileInUseError):
                storage_service.rebuild_active_export()

        result = storage_service.rebuild_active_export()

        self.assertEqual(result["new_rows"], 2)
        self.assertEqual(result["total_rows"], 2)
        self.assertTrue(os.path.exists(result["path"]))

    def test_permission_error_is_reraised_as_export_file_in_use(self):
        """openpyxl raises a bare PermissionError when Excel holds the
        file; the UI needs the typed error to show a "close it and retry"
        message instead of a traceback."""
        with patch.object(
            storage_service.Workbook, "save", side_effect=PermissionError(13, "Permission denied"),
        ):
            with self.assertRaises(storage_service.ExportFileInUseError) as ctx:
                storage_service.rebuild_active_export()

        self.assertIn("open in another program", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
