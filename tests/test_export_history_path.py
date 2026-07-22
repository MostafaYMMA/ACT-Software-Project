import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service


class ExportHistoryPathTests(unittest.TestCase):
    """
    export_history used to store only a bare filename, which left no way to
    find the file a row named. It now records the full path so the History
    page can open it -- including the case there is nothing to open.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "cards_test.db")

        patcher = patch.object(storage_service, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        storage_service.init_db()

    def _record(self, name, path):
        conn = sqlite3.connect(self.db_path)
        try:
            storage_service._record_export(conn, name, path)
            conn.commit()
        finally:
            conn.close()

    def test_returns_name_date_and_path(self):
        self._record("july.xlsx", r"C:\exports\july.xlsx")

        rows = storage_service.get_export_history()
        self.assertEqual(len(rows), 1)
        name, date_str, path = rows[0]
        self.assertEqual(name, "july.xlsx")
        self.assertTrue(date_str)
        self.assertEqual(path, r"C:\exports\july.xlsx")

    def test_path_is_none_when_there_is_no_local_file(self):
        """A finalize notice from the OTHER device names a file that was
        never written on this machine -- the UI has to be handed None, not
        a path that doesn't exist."""
        storage_service.record_finalize_from_other_device("their_export.xlsx", "2026-07-31")

        _name, _date, path = storage_service.get_export_history()[0]
        self.assertIsNone(path)

    def test_a_real_export_records_an_absolute_path_that_exists(self):
        output_path = os.path.join(self.temp_dir.name, "expenses.csv")
        storage_service.export_expenses_to_csv(output_path)

        _name, _date, path = storage_service.get_export_history()[0]
        self.assertTrue(os.path.isabs(path))
        self.assertTrue(os.path.exists(path))

    def test_rows_from_before_the_path_column_still_read_back(self):
        """_ensure_columns adds path to an existing table; those older rows
        keep a NULL path rather than breaking get_export_history."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO export_history (name, date) VALUES (?, ?)",
            ("legacy.xlsx", "2026-01-01 09:00:00"),
        )
        conn.commit()
        conn.close()

        rows = storage_service.get_export_history()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0][2])


if __name__ == "__main__":
    unittest.main()
