"""
Cross-device sync (services/storage_service.py's build_outgoing_snapshot/
apply_incoming_snapshot, exercised over a real .xlsx via
services/sync_payload_excel.py -- the same path services/outlook_service.py
attaches to a real sync mail) must never produce duplicate rows, under:
  1. the exact same sync payload applied twice,
  2. an older payload arriving after a newer one from the same device,
  3. a timecard both devices independently scanned themselves.

Two simulated devices are two entirely separate SQLite files (DB_PATH
patched to a different temp path while that "device" is active) -- the
same technique used elsewhere in this test suite, applied here across the
full two-device round trip via a real serialized payload workbook rather
than a hand-built dict, so a bug in the xlsx read/write layer would show
up here too, not just in storage_service's own in-memory tests.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service
from sync_payload_excel import write_payload_workbook, read_payload_workbook


def _entry(status, day, project_number, task, person_number, received):
    return {
        "status": status, "day": day, "project_name": "FB Test Project",
        "project_code": project_number, "task": task, "hours": "8",
        "name": "Jane", "person_number": person_number,
        "subject": f"Time Entries {status}", "sender": "s@x.com",
        "received": received, "labor_type": "L", "time_type": "R",
        "period": "2026-06-29 to 2026-07-05",
        "rate": None, "rate_updated_at": None, "rate_updated_by": None,
    }


def _row_count(db_path, table="timecards_approved"):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    finally:
        conn.close()


class TwoDeviceNoDuplicatesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_a = os.path.join(self.temp_dir.name, "device_a.db")
        self.db_b = os.path.join(self.temp_dir.name, "device_b.db")

        self.patcher = patch.object(storage_service, "DB_PATH", self.db_a)
        self.patcher.start()
        self.addCleanup(self._stop_patcher)
        storage_service.init_db()

        self._use_device(self.db_b)
        storage_service.init_db()

    def _stop_patcher(self):
        try:
            self.patcher.stop()
        except RuntimeError:
            pass

    def _use_device(self, db_path):
        self.patcher.stop()
        self.patcher = patch.object(storage_service, "DB_PATH", db_path)
        self.patcher.start()

    def _round_trip(self, snapshot):
        """Serializes to a real .xlsx and reads it back -- the same
        transformation a real sync mail's attachment goes through."""
        path = os.path.join(self.temp_dir.name, f"payload_{id(snapshot)}.xlsx")
        write_payload_workbook(snapshot, "snapshot", path)
        return read_payload_workbook(path, "snapshot")

    # -----------------------------------------------------------------
    def test_same_payload_applied_twice_no_duplicates(self):
        self._use_device(self.db_a)
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "Task A", "P1", "2026-07-01 09:00:00"),
        ])
        payload = self._round_trip(storage_service.build_outgoing_snapshot())

        self._use_device(self.db_b)
        result1 = storage_service.apply_incoming_snapshot(payload)
        result2 = storage_service.apply_incoming_snapshot(payload)

        self.assertTrue(result1["applied"])
        self.assertFalse(result2["applied"])
        self.assertEqual(result2["reason"], "superseded")
        self.assertEqual(_row_count(self.db_b), 1)
        self.assertEqual(_row_count(self.db_b, "current_sheet"), 1)

    def test_older_payload_after_newer_no_duplicates(self):
        self._use_device(self.db_a)
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "Task A", "P1", "2026-07-01 09:00:00"),
        ])
        old_payload = self._round_trip(storage_service.build_outgoing_snapshot())

        storage_service.save_cards([
            _entry("Approved", "2026-07-02", "FB-200", "Task B", "P2", "2026-07-02 09:00:00"),
        ])
        new_payload = self._round_trip(storage_service.build_outgoing_snapshot())
        self.assertGreater(int(new_payload["seq"]), int(old_payload["seq"]))
        self.assertEqual(len(new_payload["rows"]), 2)

        self._use_device(self.db_b)
        result_new = storage_service.apply_incoming_snapshot(new_payload)
        result_old = storage_service.apply_incoming_snapshot(old_payload)  # arrives late

        self.assertTrue(result_new["applied"])
        self.assertFalse(result_old["applied"])
        self.assertEqual(result_old["reason"], "superseded")
        self.assertEqual(_row_count(self.db_b), 2)
        self.assertEqual(_row_count(self.db_b, "current_sheet"), 2)

    def test_row_scanned_by_both_devices_no_duplicate(self):
        shared = _entry("Approved", "2026-07-03", "FB-300", "Task C", "P3", "2026-07-03 09:00:00")

        self._use_device(self.db_a)
        storage_service.save_cards([shared])
        payload = self._round_trip(storage_service.build_outgoing_snapshot())

        self._use_device(self.db_b)
        storage_service.save_cards([dict(shared)])  # B scanned the identical email itself
        self.assertEqual(_row_count(self.db_b), 1)
        self.assertEqual(_row_count(self.db_b, "current_sheet"), 1)

        result = storage_service.apply_incoming_snapshot(payload)
        self.assertTrue(result["applied"])
        self.assertEqual(_row_count(self.db_b), 1)
        self.assertEqual(_row_count(self.db_b, "current_sheet"), 1)

    def test_retrying_apply_after_the_workbook_is_reread_stays_safe(self):
        """Re-reading the SAME saved .xlsx file (e.g. a retried Outlook
        attachment save) and applying it again must be just as safe as
        replaying the in-memory payload -- the natural-key UNIQUE
        constraint, not just the seq gate, is what actually guarantees
        this."""
        self._use_device(self.db_a)
        storage_service.save_cards([
            _entry("Approved", "2026-07-04", "FB-400", "Task D", "P4", "2026-07-04 09:00:00"),
        ])
        snapshot = storage_service.build_outgoing_snapshot()
        path = os.path.join(self.temp_dir.name, "retry_payload.xlsx")
        write_payload_workbook(snapshot, "snapshot", path)

        self._use_device(self.db_b)
        for _ in range(3):
            payload = read_payload_workbook(path, "snapshot")
            storage_service.save_cards(payload["rows"], origin=payload["device_id"])

        self.assertEqual(_row_count(self.db_b), 1)
        self.assertEqual(_row_count(self.db_b, "current_sheet"), 1)

    def test_rate_and_row_color_reach_current_sheet_with_no_duplication(self):
        """The Part 1 fix's end-to-end guarantee, re-checked here alongside
        duplication: editing rate/colour through the real Current Sheet
        functions, syncing, and re-applying must never duplicate the row
        even though it's now carrying merged fields."""
        self._use_device(self.db_a)
        storage_service.save_cards([
            _entry("Approved", "2026-07-05", "FB-500", "Task E", "P5", "2026-07-05 09:00:00"),
        ])
        row_id = storage_service.get_current_sheet_rows()[0]["id"]
        storage_service.update_current_sheet_field(row_id, "rate", 42.0)
        storage_service.set_current_sheet_row_color(row_id, "#4A90D9")
        payload = self._round_trip(storage_service.build_outgoing_snapshot())

        self._use_device(self.db_b)
        storage_service.apply_incoming_snapshot(payload)
        storage_service.apply_incoming_snapshot(payload)  # replayed

        self.assertEqual(_row_count(self.db_b), 1)
        self.assertEqual(_row_count(self.db_b, "current_sheet"), 1)
        row = storage_service.get_current_sheet_rows()[0]
        self.assertEqual(row["rate"], 42.0)
        self.assertEqual(row["row_color"], "#4A90D9")


if __name__ == "__main__":
    unittest.main()
