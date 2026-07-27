"""
Covers Part 2 (Finalize's Save As destination) and Part 3 (auto-open) at
the services layer:

  - local_finalize/finalize_month, given save_as_path, copy the closed
    rolling-export file there (services.sync_service._copy_finalized_export)
    and repoint export_history at the destination
    (storage_service.relocate_last_export_history_entry) -- so Export
    History's double-click, and the returned "path", point at the file
    the user can actually find, not the internal auto-generated path.
  - The internal file is left in place (a COPY, not a move) -- see the
    docstring on _copy_finalized_export for why.
  - A copy failure raises clearly (with both paths in the message)
    rather than silently reporting success, while the period is still
    correctly closed (finalize_active_export already committed by then).
  - save_as_path=None (no Save As requested) behaves exactly as before --
    the returned/logged path is the internal one.

The Save As dialog itself, and the "cancelling aborts before anything
runs" behaviour, are UI-layer concerns owned by
ui/Pages/History.py::_on_finalize_clicked (and CurrentSheet.py's
equivalent) -- covered in test_history_finalize_save_as.py instead, since
they need a real QApplication/widget, not this services-only test.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service
import sync_service


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


class FinalizeSaveAsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        self.exports_dir = os.path.join(self.temp_dir.name, "exports")
        for mod, name, value in (
            (storage_service, "DB_PATH", self.db_path),
            (storage_service, "EXPORTS_DIR", self.exports_dir),
        ):
            p = patch.object(mod, name, value)
            p.start()
            self.addCleanup(p.stop)
        fx = patch.object(storage_service, "_cached_usd_rates", return_value={"AED": 3.67})
        fx.start()
        self.addCleanup(fx.stop)
        # local_finalize (and, via it, local_update) calls sync_cards --
        # a real Outlook COM inbox scan. Not what these tests are about,
        # and it hangs/fails outside a configured Outlook profile, so it's
        # stubbed to a no-op here.
        scan = patch.object(sync_service, "sync_cards", return_value=None)
        scan.start()
        self.addCleanup(scan.stop)
        storage_service.init_db()

        storage_service.save_cards([
            _entry("2026-07-01", "FB-1", "FB Kitchen", "T1", "P1", "2026-07-01 09:00:00"),
        ])

    def test_local_finalize_with_no_save_as_path_behaves_as_before(self):
        result = sync_service.local_finalize("2026-07-01", "2026-07-31")
        self.assertTrue(result["path"].startswith(self.exports_dir))
        _name, _date, logged_path = storage_service.get_export_history()[0]
        self.assertEqual(logged_path, result["path"])

    def test_local_finalize_copies_to_the_chosen_destination(self):
        destination = os.path.join(self.temp_dir.name, "chosen", "My Export.xlsx")
        os.makedirs(os.path.dirname(destination), exist_ok=True)

        result = sync_service.local_finalize("2026-07-01", "2026-07-31", save_as_path=destination)

        self.assertEqual(result["path"], destination)
        self.assertTrue(os.path.exists(destination), "the copy must actually exist at the destination")

    def test_internal_file_is_kept_not_moved(self):
        destination = os.path.join(self.temp_dir.name, "chosen.xlsx")

        # Capture the internal path Finalize would have used by patching
        # finalize_active_export's caller indirectly: just check the
        # rolling EXPORTS_DIR still has a file in it after finalize.
        result = sync_service.local_finalize("2026-07-01", "2026-07-31", save_as_path=destination)
        internal_files = os.listdir(self.exports_dir)
        self.assertTrue(
            any(f.endswith(".xlsx") for f in internal_files),
            "the internal auto-generated file must still exist (copy, not move)",
        )
        self.assertTrue(os.path.exists(destination))
        self.assertNotEqual(result["path"], os.path.join(self.exports_dir, internal_files[0]))

    def test_export_history_is_repointed_at_the_chosen_destination(self):
        destination = os.path.join(self.temp_dir.name, "chosen.xlsx")
        sync_service.local_finalize("2026-07-01", "2026-07-31", save_as_path=destination)

        name, _date, logged_path = storage_service.get_export_history()[0]
        self.assertEqual(logged_path, os.path.abspath(destination))
        self.assertEqual(name, os.path.basename(destination))

    def test_copy_failure_raises_clearly_and_leaves_the_period_closed(self):
        destination = os.path.join(self.temp_dir.name, "chosen.xlsx")

        with patch("sync_service.shutil.copy2", side_effect=OSError("disk full")):
            with self.assertRaises(RuntimeError) as ctx:
                sync_service.local_finalize("2026-07-01", "2026-07-31", save_as_path=destination)

        message = str(ctx.exception)
        self.assertIn("disk full", message)
        self.assertIn(destination, message)
        self.assertIn(self.exports_dir, message)  # names the internal path too

        # The period is still correctly (and consistently) closed --
        # finalize_active_export already committed before the copy step
        # ran, and that can't be undone without restructuring the rolling
        # export model (out of scope). The internal file is real and
        # findable; only the extra copy at the chosen destination failed.
        self.assertIsNone(storage_service.get_active_export_path())
        self.assertFalse(os.path.exists(destination))
        internal_files = os.listdir(self.exports_dir)
        self.assertEqual(len(internal_files), 1)
        self.assertTrue(os.path.exists(os.path.join(self.exports_dir, internal_files[0])))

    def test_finalize_month_also_supports_save_as(self):
        """Same contract as local_finalize, for the sync-on path -- no
        recipient configured here so the notify step is a no-op, but the
        Save As copy step must still run identically."""
        destination = os.path.join(self.temp_dir.name, "chosen_sync_on.xlsx")

        result = sync_service.finalize_month(
            recipient_email="", start_date="2026-07-01", end_date="2026-07-31", save_as_path=destination,
        )

        self.assertEqual(result["path"], destination)
        self.assertTrue(os.path.exists(destination))
        _name, _date, logged_path = storage_service.get_export_history()[0]
        self.assertEqual(logged_path, os.path.abspath(destination))


if __name__ == "__main__":
    unittest.main()
