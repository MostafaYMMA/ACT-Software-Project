"""
Covers ui/Pages/Dashboard.py's table being read-only: data can only be
edited on the Current Sheet page (storage_service.update_current_sheet_field),
not from the live Dashboard view of scanned mail.
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class DashboardPageReadOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_table_has_no_edit_triggers(self):
        from PySide6.QtWidgets import QTableWidget
        from ui.Pages.Dashboard import DashboardPage

        page = DashboardPage()
        self.assertEqual(page.table.editTriggers(), QTableWidget.EditTrigger.NoEditTriggers)
        page.deleteLater()

    def test_no_item_changed_handler_wired_to_persist_edits(self):
        # Explicit non-goal check for a regression: nothing on this page
        # should write cell edits back to the database anymore.
        from ui.Pages.Dashboard import DashboardPage

        page = DashboardPage()
        self.assertFalse(hasattr(page, "_on_item_changed"))
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
