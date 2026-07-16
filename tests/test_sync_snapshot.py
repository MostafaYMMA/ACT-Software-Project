import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service


def _entry(day="Monday, 06 Jul", received="2026-07-05 10:00:00", subject="A",
           project_number="P1", task="T1", person_number="EMP1", status="Approved",
           qty="8", name="Jane Doe"):
    return {
        "day": day, "received": received, "labor_type": "Regular", "time_type": "Billable",
        "hours": qty, "project_code": project_number, "project_name": "FB Test Project",
        "task": task, "name": name, "period": "2026-07", "person_number": person_number,
        "subject": subject, "sender": "outlook@x.com", "status": status,
    }


class TwoDeviceSyncTests(unittest.TestCase):
    """
    Simulates the real scenario: two laptops, two separate SQLite files,
    no shared server -- device A scans some timecards, builds a snapshot,
    and device B applies it. Covers the specific edge cases raised during
    the brainstorm: idempotent re-apply, out-of-order/superseded snapshots,
    rate last-write-wins, and a status change (Approved -> Rejected)
    propagating correctly.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.path_a = os.path.join(self.temp_dir.name, "device_a.db")
        self.path_b = os.path.join(self.temp_dir.name, "device_b.db")

        self.db_path_patcher = patch.object(storage_service, "DB_PATH", self.path_a)
        self.db_path_patcher.start()
        self.addCleanup(self.db_path_patcher.stop)
        storage_service.init_db()

    def _as_device_a(self):
        self.db_path_patcher.stop()
        self.db_path_patcher = patch.object(storage_service, "DB_PATH", self.path_a)
        self.db_path_patcher.start()

    def _as_device_b(self):
        self.db_path_patcher.stop()
        self.db_path_patcher = patch.object(storage_service, "DB_PATH", self.path_b)
        self.db_path_patcher.start()
        storage_service.init_db()

    # ------------------------------------------------------------------
    def test_snapshot_round_trip_merges_into_a_fresh_device(self):
        self._as_device_a()
        storage_service.save_cards([
            _entry(subject="A1", person_number="EMP1"),
            _entry(subject="A2", person_number="EMP2", day="Tuesday, 07 Jul"),
        ])
        snapshot = storage_service.build_outgoing_snapshot()
        self.assertEqual(len(snapshot["rows"]), 2)
        self.assertEqual(snapshot["seq"], 1)

        self._as_device_b()
        result = storage_service.apply_incoming_snapshot(snapshot)
        self.assertTrue(result["applied"])
        self.assertEqual(result["rows_merged"], 2)

        rows = storage_service.get_status_rows("approve")
        self.assertEqual(sorted(r["subject"] for r in rows), ["A1", "A2"])

    def test_reapplying_the_same_snapshot_does_not_duplicate(self):
        self._as_device_a()
        storage_service.save_cards([_entry(subject="A1")])
        snapshot = storage_service.build_outgoing_snapshot()

        self._as_device_b()
        storage_service.apply_incoming_snapshot(snapshot)
        result = storage_service.apply_incoming_snapshot(snapshot)  # replay
        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "superseded")
        self.assertEqual(len(storage_service.get_status_rows("approve")), 1)

    def test_an_older_queued_snapshot_is_skipped_after_a_newer_one_landed(self):
        self._as_device_a()
        storage_service.save_cards([_entry(subject="A1", person_number="EMP1")])
        old_snapshot = storage_service.build_outgoing_snapshot()  # seq 1

        storage_service.save_cards([_entry(subject="A2", person_number="EMP2", day="Tuesday, 07 Jul")])
        new_snapshot = storage_service.build_outgoing_snapshot()  # seq 2, superset of old

        self._as_device_b()
        # Simulate mail arriving out of order: newest processed first.
        storage_service.apply_incoming_snapshot(new_snapshot)
        result = storage_service.apply_incoming_snapshot(old_snapshot)
        self.assertFalse(result["applied"])
        # Both entries still present because new_snapshot was a full
        # superset, not a delta -- skipping the stale old one loses nothing.
        self.assertEqual(sorted(r["subject"] for r in storage_service.get_status_rows("approve")), ["A1", "A2"])

    def test_a_devices_own_snapshot_never_reincludes_rows_it_only_received(self):
        self._as_device_a()
        storage_service.save_cards([_entry(subject="A1")])
        snapshot = storage_service.build_outgoing_snapshot()

        self._as_device_b()
        storage_service.apply_incoming_snapshot(snapshot)
        # Device B now has A1 locally, but did NOT scan it itself -- its
        # own outgoing snapshot must not include it (no echo/amplification).
        b_snapshot = storage_service.build_outgoing_snapshot()
        self.assertEqual(b_snapshot["rows"], [])

    def test_rate_edit_on_a_synced_record_is_not_clobbered_by_an_older_incoming_edit(self):
        self._as_device_a()
        storage_service.save_cards([_entry(subject="A1", person_number="EMP1")])
        snapshot = storage_service.build_outgoing_snapshot()

        self._as_device_b()
        storage_service.apply_incoming_snapshot(snapshot)
        row = storage_service.get_status_rows("approve")[0]
        # Device B edits the rate on a record device A originally scanned.
        storage_service.update_status_record_field("approve", row["id"], "rate", 55)

        # An older rate-update message from device A (edited before B's
        # edit) must NOT overwrite B's newer edit.
        stale_payload = {
            "device_id": "device-a-fake-id",
            "status": "Approved",
            "natural_key": [row["day"], row["Project Number"], row["Task Name"], row["person_number"], row["received_month"]],
            "rate": 40,
            "rate_updated_at": "2020-01-01 00:00:00",
            "rate_updated_by": "device-a-fake-id",
        }
        applied = storage_service.apply_rate_update(stale_payload)
        self.assertFalse(applied)
        self.assertEqual(storage_service.get_status_rows("approve")[0]["rate"], 55)

    def test_status_change_after_first_sync_propagates_on_next_snapshot(self):
        self._as_device_a()
        storage_service.save_cards([_entry(subject="A1", person_number="EMP1", status="Approved",
                                            received="2026-07-05 10:00:00")])
        first_snapshot = storage_service.build_outgoing_snapshot()

        self._as_device_b()
        storage_service.apply_incoming_snapshot(first_snapshot)
        self.assertEqual(len(storage_service.get_status_rows("approve")), 1)

        # Back on device A: the approval gets revoked (moved to Rejected),
        # with a LATER received timestamp so it's recognized as the newer
        # state (see _save_row's staleness guard).
        self._as_device_a()
        storage_service.save_cards([_entry(subject="A1", person_number="EMP1", status="Rejected",
                                            received="2026-07-06 09:00:00")])
        second_snapshot = storage_service.build_outgoing_snapshot()

        self._as_device_b()
        result = storage_service.apply_incoming_snapshot(second_snapshot)
        self.assertTrue(result["applied"])
        self.assertEqual(len(storage_service.get_status_rows("approve")), 0)
        self.assertEqual(len(storage_service.get_status_rows("reject")), 1)


if __name__ == "__main__":
    unittest.main()