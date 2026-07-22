"""
The sync-off Update path has to carry the division through.

This is merge-fragile: `main` and `current-sheet` each had their own copy
of these worker classes and disagreed about whether LocalUpdateWorker took
a project_type. Merging them keeps ONE class but both call sites, so the
signature and the callers can silently drift apart -- either a TypeError
on the first sync-off Update, or (worse, because it's quiet) the Food &
Beverage / Hospitality toggle being chosen on screen and then ignored,
because rebuild_active_export runs unfiltered.
"""

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class LocalUpdateWorkerProjectTypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_the_worker_accepts_a_project_type(self):
        from ui.sync_workers import LocalUpdateWorker

        worker = LocalUpdateWorker("beverage")
        self.assertEqual(worker.project_type, "beverage")

    def test_the_worker_still_works_with_no_project_type(self):
        """'All' is a real choice, and it's spelled None."""
        from ui.sync_workers import LocalUpdateWorker

        self.assertIsNone(LocalUpdateWorker().project_type)

    def test_the_division_reaches_local_update(self):
        """Accepting the argument isn't enough -- it has to be forwarded,
        which is the half that fails silently."""
        import ui.sync_workers as sync_workers

        worker = sync_workers.LocalUpdateWorker("hospitality")
        with patch.object(sync_workers, "local_update", return_value={}) as mocked:
            worker.run()

        self.assertEqual(mocked.call_args.kwargs["project_type"], "hospitality")

    def test_history_and_current_sheet_construct_it_the_same_way(self):
        """Both pages own an Update button backed by this one class; a call
        site passing the wrong number of arguments raises the moment the
        button is clicked, which no import-time check would catch."""
        import inspect

        from ui.Pages import CurrentSheet, History

        for module in (History, CurrentSheet):
            source = inspect.getsource(module)
            self.assertIn(
                "LocalUpdateWorker(project_type_settings.project_type)", source,
                msg=f"{module.__name__} does not pass a project type to LocalUpdateWorker",
            )


if __name__ == "__main__":
    unittest.main()
