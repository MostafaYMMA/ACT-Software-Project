"""
Cross-device sync must never "echo": a row that arrives here from the
other device must not be capable of bouncing back out to them again, in
any number of Sync round trips.

Two mechanisms are supposed to prevent this (see storage_service.py):
  1. build_outgoing_snapshot() only ever selects rows whose origin column
     equals THIS device's own id -- a row saved via apply_incoming_snapshot
     is tagged with the SENDER's device id, so it can never pass that
     filter on the receiving device.
  2. apply_incoming_snapshot() dedupes per-sender by seq (get_last_applied_seq/
     _set_last_applied_seq), and the rate/colour merges are strictly
     newer-than gated (_apply_rate_if_newer/_apply_color_if_newer) -- so
     even a message that DID loop back would be a no-op, not a duplicate
     or an undo of a newer local edit.

This file tests both mechanisms directly (in-process, against
build_outgoing_snapshot/apply_incoming_snapshot), and then end-to-end
through services/sync_service.update_with_other_user -- the exact function
behind the Current Sheet page's Sync button -- using a small in-memory
fake mailbox in place of real Outlook, so the full pull-then-push
orchestration is exercised, not just the storage layer underneath it.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service
import sync_service
from sync_payload_excel import write_payload_workbook, read_payload_workbook


def _entry(status, day, project_number, task, person_number, received, project_name="FB Test Project"):
    return {
        "status": status, "day": day, "project_name": project_name,
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


class _FakeMailSystem:
    """Stands in for outlook_service.send_sync_mail/scan_sync_mails: an
    in-memory mailbox keyed by recipient email, so a full pull/push cycle
    can be driven without touching real Outlook. Every send is logged so
    tests can assert on exactly what content ever left a device."""

    def __init__(self):
        self.inboxes = {}
        self.sent_log = []  # (sender_device_id, recipient_email, kind, seq, payload)
        self.current_device_email = None

    def send(self, recipient_email, kind, payload, seq, extra_attachments=None, note=""):
        device_id = payload.get("device_id", "")
        self.inboxes.setdefault(recipient_email, []).append({
            "kind": kind, "device_id": device_id, "seq": seq,
            "payload": payload, "extra_paths": [],
            "subject": f"ACT-SYNC v1 | {kind} | {device_id} | seq={seq}",
        })
        self.sent_log.append((device_id, recipient_email, kind, seq, payload))
        return True

    def scan(self, folder_name="Inbox", limit=200):
        messages = self.inboxes.get(self.current_device_email, [])
        self.inboxes[self.current_device_email] = []
        return messages, tempfile.mkdtemp(prefix="act_fake_mail_")


class OutgoingSnapshotExcludesForeignRowsTests(unittest.TestCase):
    """Direct test of mechanism 1, no mail involved."""

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

    def test_a_row_received_from_a_never_appears_in_bs_own_outgoing_snapshot(self):
        self._use_device(self.db_a)
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "Task A", "P1", "2026-07-01 09:00:00"),
        ])
        payload = storage_service.build_outgoing_snapshot()
        self.assertEqual(len(payload["rows"]), 1)

        self._use_device(self.db_b)
        result = storage_service.apply_incoming_snapshot(payload)
        self.assertTrue(result["applied"])
        self.assertEqual(_row_count(self.db_b), 1, "B must have received A's row")

        b_outgoing = storage_service.build_outgoing_snapshot()
        self.assertEqual(
            b_outgoing["rows"], [],
            "B's own outgoing snapshot must not include a row it only knows "
            "about because A sent it -- that would echo it straight back",
        )

    def test_pushing_back_after_receiving_has_nothing_to_send(self):
        """This is what push_updates (the second half of Update-with-other-
        user) actually sees: with nothing of its own to report, it must
        report 'nothing to send' rather than mailing an empty or repeat
        snapshot."""
        self._use_device(self.db_a)
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "Task A", "P1", "2026-07-01 09:00:00"),
        ])
        payload = storage_service.build_outgoing_snapshot()

        self._use_device(self.db_b)
        storage_service.apply_incoming_snapshot(payload)

        result = sync_service.push_updates("a@example.com")
        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "nothing to send")

    def test_bs_own_row_is_still_sent_alongside_the_untouched_foreign_one(self):
        """Origin filtering must be precise, not all-or-nothing: once B has
        both a foreign (A's) row and one of its own, B's outgoing snapshot
        must contain exactly the one it owns."""
        self._use_device(self.db_a)
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "Task A", "P1", "2026-07-01 09:00:00"),
        ])
        payload = storage_service.build_outgoing_snapshot()

        self._use_device(self.db_b)
        storage_service.apply_incoming_snapshot(payload)
        storage_service.save_cards([
            _entry("Approved", "2026-07-02", "FB-200", "Task B", "P2", "2026-07-02 09:00:00"),
        ])

        b_outgoing = storage_service.build_outgoing_snapshot()
        self.assertEqual(len(b_outgoing["rows"]), 1)
        self.assertEqual(b_outgoing["rows"][0]["task"], "Task B")


class RateAndColorEchoTests(unittest.TestCase):
    """Mechanism 2: even a message that DID travel A -> B -> A again must
    be a no-op, since it's the same timestamp, not a newer one."""

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

    def test_replaying_the_same_rate_message_twice_does_not_reapply(self):
        self._use_device(self.db_a)
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "Task A", "P1", "2026-07-01 09:00:00"),
        ])
        row_id = storage_service.get_current_sheet_rows()[0]["id"]
        storage_service.update_current_sheet_field(row_id, "rate", 42.0)

        approved_id = sqlite3.connect(self.db_a).execute(
            "SELECT id FROM timecards_approved"
        ).fetchone()[0]
        rate_payload = storage_service.build_rate_update_payload("approve", approved_id)

        self._use_device(self.db_b)
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "Task A", "P1", "2026-07-01 09:00:00"),
        ])  # B scanned the same underlying timecard itself

        first = storage_service.apply_rate_update(rate_payload)
        second = storage_service.apply_rate_update(rate_payload)  # bounced back / redelivered

        self.assertTrue(first)
        self.assertFalse(second, "a message identical to one already applied must be a no-op")
        self.assertEqual(_row_count(self.db_b), 1)

    def test_a_rate_edit_bounced_through_a_full_snapshot_does_not_undo_a_newer_local_edit(self):
        """If a stale rate (older updated_at) somehow arrived back at the
        device that made a NEWER edit since, the newer edit must survive."""
        self._use_device(self.db_a)
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "Task A", "P1", "2026-07-01 09:00:00"),
        ])
        row_id = storage_service.get_current_sheet_rows()[0]["id"]
        storage_service.update_current_sheet_field(row_id, "rate", 10.0)
        stale_payload = storage_service.build_outgoing_snapshot()

        # A newer edit happens locally after the stale payload was captured.
        storage_service.update_current_sheet_field(row_id, "rate", 99.0)

        # The stale payload comes back around (e.g. a delayed duplicate).
        storage_service.apply_incoming_snapshot(
            {**stale_payload, "device_id": "some-other-device", "seq": 1}
        )

        row = storage_service.get_current_sheet_rows()[0]
        self.assertEqual(row["rate"], 99.0, "a stale bounced rate must not overwrite the newer local edit")


class FullRoundTripNoEchoTests(unittest.TestCase):
    """End-to-end through sync_service.update_with_other_user -- the real
    function behind the Sync button -- using a fake in-memory mailbox
    instead of real Outlook."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_a = os.path.join(self.temp_dir.name, "device_a.db")
        self.db_b = os.path.join(self.temp_dir.name, "device_b.db")
        self.exports_dir = os.path.join(self.temp_dir.name, "exports")

        self.mail = _FakeMailSystem()
        self.a_email = "a@example.com"
        self.b_email = "b@example.com"

        self.db_patcher = patch.object(storage_service, "DB_PATH", self.db_a)
        self.db_patcher.start()
        self.addCleanup(self._stop_db_patcher)

        self.exports_patcher = patch.object(storage_service, "EXPORTS_DIR", self.exports_dir)
        self.exports_patcher.start()
        self.addCleanup(self.exports_patcher.stop)

        self.rate_patcher = patch.object(storage_service, "_cached_usd_rates", return_value={"AED": 3.67})
        self.rate_patcher.start()
        self.addCleanup(self.rate_patcher.stop)

        self.send_patcher = patch.object(sync_service, "send_sync_mail", side_effect=self.mail.send)
        self.send_patcher.start()
        self.addCleanup(self.send_patcher.stop)

        self.scan_patcher = patch.object(sync_service, "scan_sync_mails", side_effect=self.mail.scan)
        self.scan_patcher.start()
        self.addCleanup(self.scan_patcher.stop)

        storage_service.init_db()
        self._use_device(self.db_b, self.b_email)
        storage_service.init_db()
        self._use_device(self.db_a, self.a_email)

    def _stop_db_patcher(self):
        try:
            self.db_patcher.stop()
        except RuntimeError:
            pass

    def _use_device(self, db_path, own_email):
        self.db_patcher.stop()
        self.db_patcher = patch.object(storage_service, "DB_PATH", db_path)
        self.db_patcher.start()
        self.mail.current_device_email = own_email

    def test_row_created_on_a_settles_to_one_copy_on_each_side_after_repeated_syncs(self):
        self._use_device(self.db_a, self.a_email)
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "Task A", "P1", "2026-07-01 09:00:00"),
        ])

        # A -> B -> A -> B, three full Sync-button clicks alternating sides.
        for _ in range(3):
            self._use_device(self.db_a, self.a_email)
            sync_service.update_with_other_user(self.b_email)
            self._use_device(self.db_b, self.b_email)
            sync_service.update_with_other_user(self.a_email)

        self.assertEqual(_row_count(self.db_a), 1, "the row must not have multiplied on A")
        self.assertEqual(_row_count(self.db_b), 1, "the row must not have multiplied on B")
        self.assertEqual(_row_count(self.db_a, "current_sheet"), 1)
        self.assertEqual(_row_count(self.db_b, "current_sheet"), 1)

    def test_no_snapshot_mail_ever_carries_a_row_back_to_where_it_came_from(self):
        """Inspects the fake mailbox's full send log: once B has A's row,
        no snapshot B ever sends to A may contain it again."""
        self._use_device(self.db_a, self.a_email)
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "Task A", "P1", "2026-07-01 09:00:00"),
        ])
        sync_service.update_with_other_user(self.b_email)  # A pushes to B

        self._use_device(self.db_b, self.b_email)
        sync_service.update_with_other_user(self.a_email)  # B pulls A's row, pushes back to A
        sync_service.update_with_other_user(self.a_email)  # click Sync again, nothing new

        b_to_a_snapshots = [
            entry for entry in self.mail.sent_log
            if entry[1] == self.a_email and entry[2] == "snapshot"
        ]
        for _sender_id, _recipient, _kind, _seq, payload in b_to_a_snapshots:
            self.assertEqual(
                payload["rows"], [],
                "B must never mail A's own row back to A",
            )

    def test_b_never_sends_a_non_empty_snapshot_once_both_sides_are_caught_up(self):
        """A resending its OWN unchanged data on every Sync click is
        expected (push_updates always sends its full current-period
        picture, not a delta -- and it's idempotent on B's end, see
        RateAndColorEchoTests). The actual no-echo guarantee is narrower
        and absolute: B, which owns nothing, must NEVER have a non-empty
        snapshot to send back to A, no matter how many round trips run."""
        self._use_device(self.db_a, self.a_email)
        storage_service.save_cards([
            _entry("Approved", "2026-07-01", "FB-100", "Task A", "P1", "2026-07-01 09:00:00"),
        ])
        sync_service.update_with_other_user(self.b_email)
        self._use_device(self.db_b, self.b_email)
        sync_service.update_with_other_user(self.a_email)
        self._use_device(self.db_a, self.a_email)
        sync_service.update_with_other_user(self.b_email)

        # Several more full round trips with no local changes on either side.
        for _ in range(3):
            self._use_device(self.db_b, self.b_email)
            sync_service.update_with_other_user(self.a_email)
            self._use_device(self.db_a, self.a_email)
            sync_service.update_with_other_user(self.b_email)

        # push_updates skips sending entirely when there's nothing new (see
        # sync_service.push_updates) -- B, which owns nothing, must never
        # have generated a single snapshot send to A across any of this.
        b_snapshot_sends = [
            e for e in self.mail.sent_log
            if e[1] == self.a_email and e[2] == "snapshot"
        ]
        self.assertEqual(
            b_snapshot_sends, [],
            "B has nothing of its own, so it must never mail A a snapshot at all -- "
            "not even an empty one that could later be mistaken for real data",
        )

        # And row counts must have stayed flat throughout -- the redundant
        # resends from A landing on B repeatedly must all be no-ops.
        self.assertEqual(_row_count(self.db_a), 1)
        self.assertEqual(_row_count(self.db_b), 1)


if __name__ == "__main__":
    unittest.main()
