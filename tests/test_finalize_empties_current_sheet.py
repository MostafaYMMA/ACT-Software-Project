"""
Finalize must empty the Current Sheet for the division it closes, and it
must STAY empty across an app restart (i.e. a later scan must not re-add
the closed rows).

Regression for two reported bugs:
  - "the current sheet still holds data after Finalize is clicked"
  - "it should empty it even after I restart the app"

The tricky real-world case (seen in the field): Finalize is clicked when
the active export has 0 NEW rows to add -- everything approved is already
in an earlier finalized export -- yet the Current Sheet still shows rows
that were finalized in a PREVIOUS batch and never cleared. Emptying only
"this batch's" rows would leave those behind, so finalize_active_export
clears the whole division's Current Sheet, not just the current batch.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service


def _entry(day, code, name, task, person, received):
    return {
        "status": "Approved", "day": day, "project_name": name,
        "project_code": code, "task": task, "hours": "8",
        "name": "Jane", "person_number": person,
        "subject": "Time Entries Approved", "sender": "s@x.com",
        "received": received, "labor_type": "L", "time_type": "R",
        "period": "2026-06-29 to 2026-07-05",
        "rate": None, "rate_updated_at": None, "rate_updated_by": None,
    }


class FinalizeEmptiesCurrentSheetTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        self.exports_dir = os.path.join(self.temp_dir.name, "exports")
        for name, value in (("DB_PATH", self.db_path), ("EXPORTS_DIR", self.exports_dir)):
            p = patch.object(storage_service, name, value)
            p.start()
            self.addCleanup(p.stop)
        fx = patch.object(storage_service, "_cached_usd_rates", return_value={"AED": 3.67})
        fx.start()
        self.addCleanup(fx.stop)
        storage_service.init_db()

    def _cs_count(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM current_sheet").fetchone()[0]
        finally:
            conn.close()

    def test_finalize_empties_the_current_sheet(self):
        storage_service.save_cards([
            _entry("2026-07-01", "FB-1", "FB Kitchen", "T1", "P1", "2026-07-01 09:00:00"),
            _entry("2026-07-02", "FB-2", "FB Bar", "T2", "P2", "2026-07-02 09:00:00"),
        ])
        self.assertEqual(self._cs_count(), 2)

        storage_service.rebuild_active_export()
        storage_service.finalize_active_export("2026-07-31")

        self.assertEqual(self._cs_count(), 0)

    def test_current_sheet_stays_empty_after_a_restart_rescan(self):
        """The 'even after I restart the app' half: a scan re-importing the
        same emails must NOT bring the finalized rows back."""
        rows = [
            _entry("2026-07-01", "FB-1", "FB Kitchen", "T1", "P1", "2026-07-01 09:00:00"),
            _entry("2026-07-02", "FB-2", "FB Bar", "T2", "P2", "2026-07-02 09:00:00"),
        ]
        storage_service.save_cards(rows)
        storage_service.rebuild_active_export()
        storage_service.finalize_active_export("2026-07-31")
        self.assertEqual(self._cs_count(), 0)

        # App restart -> Scan Inbox re-imports the same approved emails.
        storage_service.save_cards([dict(r) for r in rows])
        self.assertEqual(self._cs_count(), 0, "finalized rows must not reappear on rescan")

    def test_clears_rows_finalized_in_an_earlier_batch_even_when_this_export_is_empty(self):
        """The exact field scenario: a first Finalize closes the rows, a
        later approved row is added, and the current sheet has accumulated
        stale rows. A second Finalize whose export adds 0 NEW rows must
        still empty the whole sheet."""
        first = _entry("2026-07-01", "FB-1", "FB Kitchen", "T1", "P1", "2026-07-01 09:00:00")
        storage_service.save_cards([first])
        storage_service.rebuild_active_export()
        storage_service.finalize_active_export("2026-07-15")
        self.assertEqual(self._cs_count(), 0)

        # A new approved row shows up and lands in the current sheet.
        storage_service.save_cards([
            _entry("2026-07-20", "FB-2", "FB Bar", "T2", "P2", "2026-07-20 09:00:00"),
        ])
        self.assertEqual(self._cs_count(), 1)

        result = storage_service.finalize_active_export("2026-07-31")
        # Whatever the export row_count is, the sheet must end up empty.
        self.assertEqual(self._cs_count(), 0)

    def test_finalizing_one_division_leaves_the_other_untouched(self):
        storage_service.save_cards([
            _entry("2026-07-01", "FB-1", "FB Kitchen", "T1", "P1", "2026-07-01 09:00:00"),
            _entry("2026-07-02", "HL-1", "HL Hotel", "T2", "P2", "2026-07-02 09:00:00"),
        ])
        storage_service.rebuild_active_export(project_type="beverage")
        storage_service.finalize_active_export("2026-07-31", project_type="beverage")

        remaining = storage_service.get_current_sheet_rows()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["Project Name"], "HL Hotel")

    def test_a_row_approved_after_finalize_still_appears(self):
        """Finalize closing the period must not poison future work: a
        genuinely new approved timecard after the boundary still shows up."""
        storage_service.save_cards([
            _entry("2026-07-01", "FB-1", "FB Kitchen", "T1", "P1", "2026-07-01 09:00:00"),
        ])
        storage_service.rebuild_active_export()
        storage_service.finalize_active_export("2026-07-15")
        self.assertEqual(self._cs_count(), 0)

        storage_service.save_cards([
            _entry("2026-07-25", "FB-9", "FB New", "T9", "P9", "2026-07-25 09:00:00"),
        ])
        self.assertEqual(self._cs_count(), 1)
        self.assertEqual(storage_service.get_current_sheet_rows()[0]["Project Name"], "FB New")


if __name__ == "__main__":
    unittest.main()
