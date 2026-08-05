"""
Re-scanning an unchanged timecard that was forwarded again later must NOT
push its stored `received` forward. When everything about an entry matches
except the received timestamp (same day/project/task/person/received_month
AND same status), the EARLIEST received wins -- regardless of the order the
copies are scanned in, and regardless of whether they arrive in one batch
or across separate scans.

A genuine STATUS change is not "everything matches except received", so it
keeps the existing latest-wins protection: re-reading an old Pending email
must not undo an Approval that came after it.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service


def _entry(status, received, day="Saturday, 07 Mar", sender="s@x.com"):
    return {
        "status": status, "day": day, "project_name": "HL Test Project",
        "project_code": "400380981", "task": "1.01.00 - FP Labor",
        "hours": "8", "name": "Osama", "person_number": "1960153",
        "subject": "Fwd: FW: Timecard& expenses.", "sender": sender,
        "received": received, "labor_type": "ORCL", "time_type": "AE",
        "period": "3/7/26 - 3/13/26",
        "rate": None, "rate_updated_at": None, "rate_updated_by": None,
    }


LATE = "2026-07-26 12:15:11+00:00"
MID = "2026-07-16 12:07:47+00:00"
EARLY = "2026-07-15 14:29:46+00:00"


class ReceivedEarliestWinsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db = os.path.join(self.temp_dir.name, "cards.db")
        self.patcher = patch.object(storage_service, "DB_PATH", self.db)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        storage_service.init_db()

    def _received(self, table="timecards_approved"):
        conn = sqlite3.connect(self.db)
        try:
            return [r[0] for r in conn.execute(f'SELECT received FROM "{table}"')]
        finally:
            conn.close()

    def _count(self, table="timecards_approved"):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        finally:
            conn.close()

    def test_later_rescan_does_not_move_received_forward(self):
        storage_service.save_cards([_entry("Approved", EARLY)])
        storage_service.save_cards([_entry("Approved", LATE)])  # re-forwarded later
        self.assertEqual(self._count(), 1)
        self.assertEqual(self._received(), [EARLY])

    def test_earlier_copy_arriving_later_pulls_received_back(self):
        # Newest copy seen first (real scans walk newest-first), earlier copy
        # arrives after -- the earliest must still win.
        storage_service.save_cards([_entry("Approved", LATE)])
        storage_service.save_cards([_entry("Approved", EARLY)])
        self.assertEqual(self._count(), 1)
        self.assertEqual(self._received(), [EARLY])

    def test_all_copies_in_one_batch_pick_earliest(self):
        storage_service.save_cards([
            _entry("Approved", LATE),
            _entry("Approved", MID),
            _entry("Approved", EARLY),
        ])
        self.assertEqual(self._count(), 1)
        self.assertEqual(self._received(), [EARLY])

    def test_status_change_still_latest_wins(self):
        # Approval (later) is the current state; an older Pending re-read
        # afterwards must not drag it back to pending.
        storage_service.save_cards([_entry("Approved", LATE)])
        storage_service.save_cards([_entry("Pending", EARLY)])
        self.assertEqual(self._count("timecards_approved"), 1)
        self.assertEqual(self._count("timecards_pending"), 0)
        self.assertEqual(self._received("timecards_approved"), [LATE])

    def test_different_sender_is_treated_as_different_entry(self):
        storage_service.save_cards([_entry("Approved", EARLY, sender="one@example.com")])
        storage_service.save_cards([_entry("Approved", MID, sender="two@example.com")])
        self.assertEqual(self._count(), 2)

    def test_forward_status_change_moves_and_keeps_new_received(self):
        # Pending first, Approval later: entry moves to approved with the
        # approval's received (a genuine new event, not a re-forward).
        storage_service.save_cards([_entry("Pending", EARLY)])
        storage_service.save_cards([_entry("Approved", LATE)])
        self.assertEqual(self._count("timecards_pending"), 0)
        self.assertEqual(self._count("timecards_approved"), 1)
        self.assertEqual(self._received("timecards_approved"), [LATE])


if __name__ == "__main__":
    unittest.main()
