import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service


class CurrentSheetSyncTests(unittest.TestCase):
    """
    The Current Sheet is a working copy the user edits by hand, so the one
    rule that matters is that a re-scan can only ever ADD rows: every value
    corrected and every colour set has to survive untouched, exactly like
    invoice_lines' manual columns do.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "cards_test.db")

        patcher = patch.object(storage_service, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        storage_service.init_db()

    def _insert_approved(self, day, project_name="FB Kitchen", qty="8"):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'INSERT INTO timecards_approved '
            '(day, "Date", subject, sender, "Project Number", "Project Name", '
            '"Task Name", "Qty", rate, period, received, received_month, person_number) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (day, "2026-07-06", "S", "s@x.com", "P1", project_name, "T1", qty, 0,
             "from 2026-07-06 to 2026-07-12", "2026-07-06 10:00:00", "2026-07", "PN1"),
        )
        conn.commit()
        row_id = conn.execute("SELECT id FROM timecards_approved WHERE day = ?", (day,)).fetchone()[0]
        conn.close()
        return row_id

    def _sync(self):
        conn = sqlite3.connect(self.db_path)
        try:
            storage_service.sync_current_sheet(conn)
            conn.commit()
        finally:
            conn.close()

    def test_seeds_one_row_per_approved_timecard(self):
        self._insert_approved("Monday")
        self._insert_approved("Tuesday")

        self._sync()

        rows = storage_service.get_current_sheet_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["day"] for row in rows}, {"Monday", "Tuesday"})
        self.assertTrue(all(row["row_color"] is None for row in rows))

    def test_resync_adds_nothing_for_rows_already_present(self):
        self._insert_approved("Monday")
        self._sync()
        self._sync()
        self._sync()

        self.assertEqual(len(storage_service.get_current_sheet_rows()), 1)

    def test_resync_picks_up_newly_approved_rows(self):
        self._insert_approved("Monday")
        self._sync()
        self._insert_approved("Tuesday")
        self._sync()

        self.assertEqual(len(storage_service.get_current_sheet_rows()), 2)

    def test_a_manual_edit_survives_every_later_resync(self):
        """The whole reason this table exists rather than reading
        timecards_approved directly."""
        self._insert_approved("Monday", qty="8")
        self._sync()
        row_id = storage_service.get_current_sheet_rows()[0]["id"]

        storage_service.update_current_sheet_field(row_id, "Task Name", "Corrected by hand")
        self._sync()

        row = storage_service.get_current_sheet_rows()[0]
        self.assertEqual(row["Task Name"], "Corrected by hand")

    def test_a_row_color_survives_every_later_resync(self):
        self._insert_approved("Monday")
        self._sync()
        row_id = storage_service.get_current_sheet_rows()[0]["id"]

        storage_service.set_current_sheet_row_color(row_id, "#FFD966")
        self._sync()

        self.assertEqual(storage_service.get_current_sheet_rows()[0]["row_color"], "#FFD966")

    def test_edits_do_not_leak_back_into_the_scanned_record(self):
        """current_sheet is a working copy; timecards_approved stays the
        record of what actually arrived."""
        timecard_id = self._insert_approved("Monday")
        self._sync()
        row_id = storage_service.get_current_sheet_rows()[0]["id"]

        storage_service.update_current_sheet_field(row_id, "Task Name", "Changed")

        conn = sqlite3.connect(self.db_path)
        stored = conn.execute(
            'SELECT "Task Name" FROM timecards_approved WHERE id = ?', (timecard_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(stored, "T1")

    def test_save_cards_seeds_the_sheet(self):
        """sync_current_sheet has to actually be wired into the scan path,
        not just exist."""
        # The shape extract() produces and _to_row() consumes -- project_code
        # /task/hours, not the "Project Number"/"Task Name"/"Qty" column
        # names, which would store NULLs into the natural key's own fields.
        storage_service.save_cards([{
            "status": "Approved", "day": "Monday",
            "project_code": "P1", "project_name": "FB Kitchen", "task": "T1",
            "hours": "8", "person_number": "PN1", "subject": "S", "sender": "s@x.com",
            "received": "2026-07-06 10:00:00", "period": "from 2026-07-06 to 2026-07-12",
            "name": "N", "labor_type": "L", "time_type": "T",
        }])

        rows = storage_service.get_current_sheet_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Project Number"], "P1")
        self.assertEqual(rows[0]["Task Name"], "T1")


class CurrentSheetFieldValidationTests(unittest.TestCase):
    """update_current_sheet_field mirrors update_status_record_field: the
    column name is interpolated into the SQL, so refusing anything not on
    the table is what keeps that safe."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "cards_test.db")
        patcher = patch.object(storage_service, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        storage_service.init_db()

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'INSERT INTO current_sheet (timecard_id, day, "Task Name") VALUES (1, ?, ?)',
            ("Monday", "T1"),
        )
        conn.commit()
        conn.close()
        self.row_id = storage_service.get_current_sheet_rows()[0]["id"]

    def test_rejects_an_unknown_column(self):
        self.assertFalse(
            storage_service.update_current_sheet_field(self.row_id, "not_a_column", "x")
        )

    def test_rejects_the_internal_columns(self):
        for column in ("id", "timecard_id", "row_color"):
            self.assertFalse(
                storage_service.update_current_sheet_field(self.row_id, column, "x"),
                msg=f"{column} should not be editable as a cell",
            )

    def test_rejects_a_missing_row(self):
        self.assertFalse(
            storage_service.update_current_sheet_field(9999, "Task Name", "x")
        )

    def test_accepts_a_real_column(self):
        self.assertTrue(
            storage_service.update_current_sheet_field(self.row_id, "Task Name", "x")
        )
        self.assertEqual(storage_service.get_current_sheet_rows()[0]["Task Name"], "x")

    def test_color_can_be_set_and_cleared(self):
        self.assertTrue(storage_service.set_current_sheet_row_color(self.row_id, "#4CAF50"))
        self.assertEqual(storage_service.get_current_sheet_rows()[0]["row_color"], "#4CAF50")

        self.assertTrue(storage_service.set_current_sheet_row_color(self.row_id, None))
        self.assertIsNone(storage_service.get_current_sheet_rows()[0]["row_color"])


if __name__ == "__main__":
    unittest.main()
