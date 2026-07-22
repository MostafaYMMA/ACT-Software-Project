import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service


# The shape extract() produces and _to_row() consumes -- project_code /
# task / hours, NOT the "Project Number" / "Task Name" / "Qty" column
# names. Using the column names here silently stores NULLs into the very
# fields the natural key is built from, and every key lookup then misses.
_ENTRY = {
    "status": "Approved", "day": "Monday",
    "project_code": "P1", "project_name": "FB Kitchen", "task": "T1",
    "hours": "8", "person_number": "PN1", "subject": "S", "sender": "s@x.com",
    "received": "2026-07-06 10:00:00", "period": "from 2026-07-06 to 2026-07-12",
    "name": "N", "labor_type": "L", "time_type": "T",
}


class ColorSyncTests(unittest.TestCase):
    """
    Row colours are shared state: a highlight one user sets has to show up
    for the other. They ride out inside the ordinary snapshot and are
    merged last-write-wins, the same way a rate edit is -- so the cases
    that matter are "does it travel", "does the newer edit win", and "does
    an old replayed payload leave a newer local choice alone".
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "cards_test.db")

        patcher = patch.object(storage_service, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        storage_service.init_db()

    def _seed_approved(self):
        storage_service.save_cards([dict(_ENTRY)])
        return storage_service.get_current_sheet_rows()[0]["id"]

    def _color_of(self):
        return storage_service.get_current_sheet_rows()[0]["row_color"]

    def _stamp(self, row_id, hex_color, updated_at, updated_by="other-device"):
        """Writes a colour with an explicit timestamp, standing in for an
        edit made on another machine at a particular moment."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE current_sheet SET row_color = ?, color_updated_at = ?, color_updated_by = ? "
            "WHERE id = ?",
            (hex_color, updated_at, updated_by, row_id),
        )
        conn.commit()
        conn.close()

    # -- outgoing -----------------------------------------------------
    def test_a_colour_rides_out_in_the_snapshot(self):
        row_id = self._seed_approved()
        storage_service.set_current_sheet_row_color(row_id, "#4CAF50")

        snapshot = storage_service.build_outgoing_snapshot(since_date=None)

        self.assertEqual(len(snapshot["rows"]), 1)
        row = snapshot["rows"][0]
        self.assertEqual(row["row_color"], "#4CAF50")
        self.assertTrue(row["color_updated_at"])
        self.assertEqual(row["color_updated_by"], storage_service.get_device_id())

    def test_an_uncoloured_row_carries_no_colour_stamp(self):
        """Only an actual choice should travel -- otherwise every snapshot
        would assert 'no colour' over the other side's highlights."""
        self._seed_approved()

        row = storage_service.build_outgoing_snapshot(since_date=None)["rows"][0]

        self.assertIsNone(row["row_color"])
        self.assertIsNone(row["color_updated_at"])

    def test_pending_rows_never_pick_up_a_colour(self):
        """current_sheet.timecard_id indexes timecards_approved only; the
        other status tables have their own autoincrements, so a naive match
        on the number alone would staple an unrelated row's colour on."""
        row_id = self._seed_approved()
        storage_service.set_current_sheet_row_color(row_id, "#4CAF50")
        storage_service.save_cards([dict(_ENTRY, status="Pending", day="Tuesday",
                                         received="2026-07-07 10:00:00")])

        rows = storage_service.build_outgoing_snapshot(since_date=None)["rows"]
        pending = [row for row in rows if row["status"] == "Pending"]

        self.assertEqual(len(pending), 1)
        self.assertIsNone(pending[0]["row_color"])

    # -- incoming -----------------------------------------------------
    def _incoming(self, hex_color, updated_at, seq=1):
        return {
            "device_id": "other-device", "seq": seq,
            "generated_at": "2026-07-08 12:00:00", "period_start": None,
            "rows": [dict(
                _ENTRY,
                row_color=hex_color, color_updated_at=updated_at,
                color_updated_by="other-device",
            )],
        }

    def test_an_incoming_colour_is_applied(self):
        result = storage_service.apply_incoming_snapshot(
            self._incoming("#E05252", "2026-07-08 09:00:00")
        )

        self.assertTrue(result["applied"])
        self.assertEqual(result["colors_applied"], 1)
        self.assertEqual(self._color_of(), "#E05252")

    def test_a_newer_incoming_colour_overwrites_an_older_local_one(self):
        row_id = self._seed_approved()
        self._stamp(row_id, "#4CAF50", "2026-07-08 08:00:00", updated_by="me")

        storage_service.apply_incoming_snapshot(
            self._incoming("#E05252", "2026-07-08 09:00:00")
        )

        self.assertEqual(self._color_of(), "#E05252")

    def test_an_older_incoming_colour_leaves_a_newer_local_one_alone(self):
        """A replayed/late payload must not undo a choice made since."""
        row_id = self._seed_approved()
        self._stamp(row_id, "#4CAF50", "2026-07-08 10:00:00", updated_by="me")

        storage_service.apply_incoming_snapshot(
            self._incoming("#E05252", "2026-07-08 09:00:00")
        )

        self.assertEqual(self._color_of(), "#4CAF50")

    def test_a_cleared_colour_travels_as_a_real_edit(self):
        """Removing a highlight is a decision too -- a newer 'cleared' has
        to beat an older 'set', not be ignored as 'nothing to say'."""
        row_id = self._seed_approved()
        self._stamp(row_id, "#4CAF50", "2026-07-08 08:00:00", updated_by="me")

        storage_service.apply_incoming_snapshot(
            self._incoming(None, "2026-07-08 09:00:00")
        )

        self.assertIsNone(self._color_of())

    def test_a_colour_for_a_timecard_this_device_never_saw_is_ignored(self):
        """apply_incoming_snapshot saves the rows first, so this normally
        can't happen -- but _apply_color_if_newer must not blow up or
        colour the wrong row if the record isn't there."""
        conn = sqlite3.connect(self.db_path)
        try:
            applied = storage_service._apply_color_if_newer(
                conn, status_label="Approved",
                natural_key=("Nonexistent", "P9", "T9", "PN9", "2026-07"),
                row_color="#E05252", updated_at="2026-07-08 09:00:00",
                updated_by="other-device",
            )
        finally:
            conn.close()

        self.assertFalse(applied)

    def test_colour_survives_the_round_trip_through_the_xlsx_payload(self):
        """The email channel renders the snapshot to a real .xlsx; the new
        columns are appended to SNAPSHOT_COLUMNS, which that writer/reader
        pair zips positionally."""
        from sync_payload_excel import write_payload_workbook, read_payload_workbook

        row_id = self._seed_approved()
        storage_service.set_current_sheet_row_color(row_id, "#9B59B6")
        snapshot = storage_service.build_outgoing_snapshot(since_date=None)

        path = os.path.join(self.temp_dir.name, "payload.xlsx")
        write_payload_workbook(snapshot, "snapshot", path)
        restored = read_payload_workbook(path, "snapshot")

        self.assertEqual(restored["rows"][0]["row_color"], "#9B59B6")
        self.assertTrue(restored["rows"][0]["color_updated_at"])


if __name__ == "__main__":
    unittest.main()
