"""
UI-layer coverage for ui/Pages/History.py's Finalize button:

  - Part 2: the Save As dialog is shown after the "are you sure" confirm,
    and BEFORE anything is actually touched -- cancelling it aborts the
    whole finalize (nothing closed out, boundary not advanced, active
    export pointer not cleared), rather than finalizing anyway.
  - Part 3: a successful finalize opens the resulting file automatically
    (_open_finalized_file), via QDesktopServices -- and a failure to open
    it is NOT treated as the finalize itself failing.
  - Part 4: _on_export_row_activated is hardened against every listed
    failure mode with a specific message, never a silent no-op or an
    unhandled exception (except for the two genuinely-safe no-ops: no
    item, or a row index beyond what's loaded).

Uses a real QApplication (offscreen) and a real temp DB so
get_active_export_path/get_last_export_date reflect real, unmutated
state after a cancelled dialog -- not just "the mock wasn't called".
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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


class HistoryFinalizeSaveAsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

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

        storage_service.save_cards([
            _entry("2026-07-01", "FB-1", "FB Kitchen", "T1", "P1", "2026-07-01 09:00:00"),
        ])
        storage_service.rebuild_active_export()

    def _build_page(self):
        from ui.Pages.History import HistoryPage

        return HistoryPage()

    # -- Part 2: cancelling the Save As dialog aborts the whole finalize --

    def test_cancelling_save_as_leaves_nothing_half_finalized(self):
        page = self._build_page()
        active_before = storage_service.get_active_export_path()
        last_export_before = storage_service.get_last_export_date()
        self.assertIsNotNone(active_before, "sanity: an active export must exist before Finalize")

        with patch("ui.Pages.History.QMessageBox.question", return_value=__import__(
            "PySide6.QtWidgets", fromlist=["QMessageBox"]
        ).QMessageBox.StandardButton.Yes):
            with patch("ui.Pages.History.QFileDialog.getSaveFileName", return_value=("", "")):
                with patch("ui.Pages.History.FinalizeWorker") as mock_worker, \
                     patch("ui.Pages.History.LocalFinalizeWorker") as mock_local_worker:
                    page._on_finalize_clicked()

        mock_worker.assert_not_called()
        mock_local_worker.assert_not_called()
        self.assertEqual(storage_service.get_active_export_path(), active_before)
        self.assertEqual(storage_service.get_last_export_date(), last_export_before)

    def test_confirming_save_as_passes_the_chosen_path_to_the_worker(self):
        page = self._build_page()
        chosen = os.path.join(self.temp_dir.name, "chosen.xlsx")

        with patch("ui.Pages.History.QMessageBox.question", return_value=__import__(
            "PySide6.QtWidgets", fromlist=["QMessageBox"]
        ).QMessageBox.StandardButton.Yes):
            with patch("ui.Pages.History.QFileDialog.getSaveFileName", return_value=(chosen, "")):
                with patch("ui.Pages.History.LocalFinalizeWorker") as mock_local_worker:
                    mock_instance = MagicMock()
                    mock_local_worker.return_value = mock_instance
                    page._on_finalize_clicked()

        mock_local_worker.assert_called_once()
        _args, kwargs = mock_local_worker.call_args
        self.assertEqual(kwargs.get("save_as_path"), chosen)

    # -- Part 3: auto-open on success -----------------------------------

    def test_open_finalized_file_uses_qdesktopservices(self):
        page = self._build_page()
        with patch("ui.Pages.History.QDesktopServices.openUrl", return_value=True) as mock_open:
            page._open_finalized_file(r"C:\somewhere\export.xlsx")
        mock_open.assert_called_once()

    def test_open_finalized_file_failure_shows_info_not_error(self):
        page = self._build_page()
        with patch("ui.Pages.History.QDesktopServices.openUrl", return_value=False):
            with patch("ui.Pages.History.QMessageBox.information") as mock_info, \
                 patch("ui.Pages.History.QMessageBox.warning") as mock_warning:
                page._open_finalized_file(r"C:\somewhere\export.xlsx")
        mock_info.assert_called_once()
        mock_warning.assert_not_called()

    def test_open_finalized_file_no_path_is_a_safe_noop(self):
        page = self._build_page()
        with patch("ui.Pages.History.QDesktopServices.openUrl") as mock_open:
            page._open_finalized_file("")
        mock_open.assert_not_called()

    # -- Part 4: double-click hardening -----------------------------------

    def _page_with_row(self, path):
        page = self._build_page()
        page._export_paths = [path]
        page.table.setRowCount(1)
        from PySide6.QtWidgets import QTableWidgetItem
        item = QTableWidgetItem("some_export.xlsx")
        page.table.setItem(0, 0, item)
        return page, item

    def test_double_click_with_no_item_is_a_safe_noop(self):
        page = self._build_page()
        with patch("ui.Pages.History.QMessageBox") as mock_box:
            page._on_export_row_activated(None)
        mock_box.information.assert_not_called()
        mock_box.warning.assert_not_called()

    def test_double_click_row_index_beyond_loaded_records_is_a_safe_noop(self):
        page, item = self._page_with_row("somewhere.xlsx")
        # Force an out-of-range row on the item's reported row().
        fake_item = MagicMock()
        fake_item.row.return_value = 99
        with patch("ui.Pages.History.QMessageBox") as mock_box:
            page._on_export_row_activated(fake_item)
        mock_box.information.assert_not_called()
        mock_box.warning.assert_not_called()

    def test_double_click_null_path_shows_no_file_recorded_message(self):
        page, item = self._page_with_row(None)
        with patch("ui.Pages.History.QMessageBox.information") as mock_info:
            page._on_export_row_activated(item)
        mock_info.assert_called_once()
        self.assertIn("No file recorded", mock_info.call_args.args[1])

    def test_double_click_missing_file_shows_file_not_found_message(self):
        missing = os.path.join(self.temp_dir.name, "does_not_exist.xlsx")
        page, item = self._page_with_row(missing)
        with patch("ui.Pages.History.QMessageBox.warning") as mock_warn:
            page._on_export_row_activated(item)
        mock_warn.assert_called_once()
        self.assertIn("File not found", mock_warn.call_args.args[1])
        self.assertIn(missing, mock_warn.call_args.args[2])

    def test_double_click_directory_path_shows_not_a_file_message(self):
        directory = os.path.join(self.temp_dir.name, "a_folder")
        os.makedirs(directory)
        page, item = self._page_with_row(directory)
        with patch("ui.Pages.History.QMessageBox.warning") as mock_warn:
            page._on_export_row_activated(item)
        mock_warn.assert_called_once()
        self.assertIn("Not a file", mock_warn.call_args.args[1])

    def test_double_click_openurl_false_shows_could_not_open_message(self):
        real_file = os.path.join(self.temp_dir.name, "real.xlsx")
        with open(real_file, "wb") as f:
            f.write(b"not a real xlsx but exists")
        page, item = self._page_with_row(real_file)
        with patch("ui.Pages.History.QDesktopServices.openUrl", return_value=False):
            with patch("ui.Pages.History.QMessageBox.warning") as mock_warn:
                page._on_export_row_activated(item)
        mock_warn.assert_called_once()
        self.assertIn("Couldn't open the file", mock_warn.call_args.args[1])

    def test_double_click_openurl_true_shows_no_dialog(self):
        real_file = os.path.join(self.temp_dir.name, "real2.xlsx")
        with open(real_file, "wb") as f:
            f.write(b"not a real xlsx but exists")
        page, item = self._page_with_row(real_file)
        with patch("ui.Pages.History.QDesktopServices.openUrl", return_value=True):
            with patch("ui.Pages.History.QMessageBox") as mock_box:
                page._on_export_row_activated(item)
        mock_box.information.assert_not_called()
        mock_box.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
