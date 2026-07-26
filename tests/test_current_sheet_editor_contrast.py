"""
The Current Sheet's inline cell editor (double-click to edit) used to be a
bare QStyledItemDelegate default editor: an unstyled QLineEdit with an
empty stylesheet and Qt's raw factory palette (white background, black
text) -- completely disconnected from both the app's live theme and the
row's own colour tint. Confirmed by inspecting a real editor instance
before the fix: styleSheet() == '', palette Base/Text == #ffffff/#000000
regardless of dark mode or row_color. On a dark theme or a dark row_color
that reads as illegible dark-on-dark text -- "typing is invisible".

RowTintDelegate.createEditor() now styles the editor explicitly (QSS AND
QPalette, matching ui/account_page.py's documented QLineEdit-legibility
fix) to match whichever background the cell is actually showing: the
row's tint (via readable_text_color's WCAG contrast pick) when set, or
the theme's normal SURFACE/TEXT_PRIMARY otherwise.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _entry(day, code, name, task, person, received):
    return {
        "status": "Approved", "day": day, "project_name": name,
        "project_code": code, "task": task, "hours": "8",
        "name": "Jane", "person_number": person,
        "subject": "Time Entries Approved", "sender": "s@x.com",
        "received": received, "labor_type": "L", "time_type": "R",
        "period": "p", "rate": None, "rate_updated_at": None, "rate_updated_by": None,
    }


class CurrentSheetEditorContrastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        import storage_service as ss

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        db = os.path.join(self.temp_dir.name, "t.db")
        p = patch.object(ss, "DB_PATH", db)
        p.start()
        self.addCleanup(p.stop)
        ss.init_db()
        self.storage_service = ss

    def _open_editor(self, page, table, project_name):
        col = None
        row = None
        for r in range(table.rowCount()):
            for c in range(table.columnCount()):
                item = table.item(r, c)
                if item is not None and item.text() == project_name:
                    row, col = r, c
        self.assertIsNotNone(row, f"row for {project_name!r} not found in table")
        table.editItem(table.item(row, col))
        self.app.processEvents()
        editor = table.indexWidget(table.model().index(row, col))
        self.assertIsNotNone(editor, "double-click edit did not produce an editor widget")
        return editor

    def test_editor_on_a_tinted_row_matches_the_tint_with_readable_text(self):
        from PySide6.QtGui import QColor, QPalette
        from ui.Pages.CurrentSheet import CurrentSheetPage, readable_text_color

        self.storage_service.save_cards([
            _entry("2026-07-01", "FB-100", "FB Kitchen", "T1", "P1", "2026-07-01 09:00:00"),
        ])
        # The page now only shows rows already written into an active export
        # (Update/Finalize), so mirror an Update here before building it.
        self.storage_service.rebuild_active_export()
        row_id = self.storage_service.get_current_sheet_rows()[0]["id"]
        tint = "#9B59B6"
        self.storage_service.set_current_sheet_row_color(row_id, tint)

        page = CurrentSheetPage()
        page.show()
        self.app.processEvents()

        editor = self._open_editor(page, page.table, "FB Kitchen")
        pal = editor.palette()
        expected_text = readable_text_color(QColor(tint))

        self.assertEqual(pal.color(QPalette.ColorRole.Base).name(), QColor(tint).name())
        self.assertEqual(pal.color(QPalette.ColorRole.Text).name(), QColor(expected_text).name())
        self.assertNotEqual(
            pal.color(QPalette.ColorRole.Base).name(), pal.color(QPalette.ColorRole.Text).name(),
            "editor background and text must never be the same color",
        )

    def test_editor_on_an_untinted_row_matches_the_theme_not_qt_defaults(self):
        from PySide6.QtGui import QColor, QPalette
        from ui.Pages.CurrentSheet import CurrentSheetPage
        from ui.theme_manager import theme_manager

        theme_manager.set_mode("dark")
        self.addCleanup(theme_manager.set_mode, "light")

        self.storage_service.save_cards([
            _entry("2026-07-01", "FB-100", "FB Kitchen", "T1", "P1", "2026-07-01 09:00:00"),
        ])
        # The page now only shows rows already written into an active export
        # (Update/Finalize), so mirror an Update here before building it.
        self.storage_service.rebuild_active_export()

        page = CurrentSheetPage()
        page.show()
        self.app.processEvents()

        editor = self._open_editor(page, page.table, "FB Kitchen")
        pal = editor.palette()
        colors = theme_manager.colors()

        # The bug this guards against: Qt's raw factory default palette
        # for an unstyled QLineEdit is white-on-black text (#ffffff base,
        # #000000 text) -- neither of which is this app's dark theme.
        self.assertNotEqual(pal.color(QPalette.ColorRole.Base).name(), "#ffffff")
        self.assertEqual(pal.color(QPalette.ColorRole.Base).name(), QColor(colors["SURFACE"]).name())
        self.assertEqual(pal.color(QPalette.ColorRole.Text).name(), QColor(colors["TEXT_PRIMARY"]).name())

    def test_editor_stylesheet_is_never_empty(self):
        """The exact symptom that was directly observed and is being
        regression-tested: editor.styleSheet() == '' meant Qt fell all the
        way back to its unthemed factory palette."""
        from ui.Pages.CurrentSheet import CurrentSheetPage

        self.storage_service.save_cards([
            _entry("2026-07-01", "FB-100", "FB Kitchen", "T1", "P1", "2026-07-01 09:00:00"),
        ])
        # The page now only shows rows already written into an active export
        # (Update/Finalize), so mirror an Update here before building it.
        self.storage_service.rebuild_active_export()

        page = CurrentSheetPage()
        page.show()
        self.app.processEvents()

        editor = self._open_editor(page, page.table, "FB Kitchen")
        self.assertNotEqual(editor.styleSheet(), "")


if __name__ == "__main__":
    unittest.main()
