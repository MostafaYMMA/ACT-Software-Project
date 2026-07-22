import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

# Must be set before QApplication is created -- these tests run with no
# display and never show a window.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage_service


class CurrentSheetPageTests(unittest.TestCase):
    """
    Smoke-level coverage of the page itself: that it builds, renders what
    storage returns, and that an edit typed into a cell reaches the
    database (the itemChanged path, which is easy to break by mishandling
    the _populating_table guard).
    """

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "cards_test.db")

        patcher = patch.object(storage_service, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        storage_service.init_db()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'INSERT INTO current_sheet (timecard_id, day, "Date", "Project Name", "Task Name", "Qty") '
            'VALUES (1, ?, ?, ?, ?, ?)',
            ("Monday", "2026-07-06", "FB Kitchen", "T1", "8"),
        )
        conn.commit()
        conn.close()

    def _page(self):
        from ui.Pages.CurrentSheet import CurrentSheetPage

        return CurrentSheetPage()

    def _drain(self, ms):
        """Runs the Qt event loop so timer-driven work actually happens --
        the colour ripple paints a cell at a time (see _ripple_row_color),
        so without this only the first cell is coloured."""
        from PySide6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    @staticmethod
    def _column_of(page, name):
        """On-screen index of a data column -- the colour-picker column is
        prepended, so it isn't the same as the index in _displayed_columns."""
        from ui.Pages.CurrentSheet import _DATA_COLUMN_OFFSET

        return page._displayed_columns.index(name) + _DATA_COLUMN_OFFSET

    def test_page_builds_and_renders_the_rows(self):
        page = self._page()
        self.assertEqual(page.table.rowCount(), 1)
        self.assertGreater(page.table.columnCount(), 0)

    def test_row_color_is_not_shown_as_a_column(self):
        page = self._page()
        self.assertNotIn("row_color", page._displayed_columns)

    def test_internal_ids_are_not_shown_as_columns(self):
        """table_utils.HIDDEN_COLUMNS already covers both -- this pins that
        the page actually routes its columns through order_columns."""
        page = self._page()
        self.assertNotIn("id", page._displayed_columns)
        self.assertNotIn("timecard_id", page._displayed_columns)

    def test_editing_a_cell_persists_it(self):
        page = self._page()
        column_index = self._column_of(page, "Task Name")

        page.table.item(0, column_index).setText("Edited in the grid")

        self.assertEqual(
            storage_service.get_current_sheet_rows()[0]["Task Name"], "Edited in the grid"
        )

    def test_a_non_numeric_qty_is_reverted_not_saved(self):
        page = self._page()
        column_index = self._column_of(page, "Qty")

        page.table.item(0, column_index).setText("not a number")

        self.assertEqual(storage_service.get_current_sheet_rows()[0]["Qty"], "8")
        self.assertEqual(page.table.item(0, column_index).text(), "8")

    def test_the_leading_column_holds_a_colour_picker_button(self):
        from PySide6.QtWidgets import QToolButton
        from ui.Pages.CurrentSheet import _COLOR_COLUMN

        page = self._page()
        button = page.swatch_button(0)

        self.assertIsInstance(button, QToolButton)
        self.assertIsNotNone(button.menu(), "the button must drop down the palette")

    def test_exactly_one_swatch_per_row_however_often_it_refreshes(self):
        """refresh() used to build the picker column with insertColumn(),
        which SHIFTS existing cell widgets right rather than replacing
        them: every refresh left the last one behind and added another, so
        a row sprouted a new swatch on each page visit, scan, or update."""
        from PySide6.QtWidgets import QToolButton

        page = self._page()

        def swatches_in_row(row_index):
            found = 0
            for column in range(page.table.columnCount()):
                widget = page.table.cellWidget(row_index, column)
                if widget is None:
                    continue
                if isinstance(widget, QToolButton) or widget.findChild(QToolButton):
                    found += 1
            return found

        self.assertEqual(swatches_in_row(0), 1)

        columns_before = page.table.columnCount()
        for _ in range(4):
            page.refresh()
            self.assertEqual(
                swatches_in_row(0), 1,
                msg="a refresh added a second colour picker to the row",
            )
        self.assertEqual(
            page.table.columnCount(), columns_before,
            msg="a refresh changed the column count",
        )

    def test_the_swatch_is_a_circle(self):
        """A circle, not a rounded box -- border-radius has to be half the
        button's width, and the button has to be square for that to read as
        a circle at all."""
        from ui.Pages.CurrentSheet import _SWATCH_DIAMETER

        page = self._page()
        button = page.swatch_button(0)

        self.assertEqual(button.width(), button.height(), "the swatch is not square")
        self.assertEqual(button.width(), _SWATCH_DIAMETER)
        self.assertIn(f"border-radius: {_SWATCH_DIAMETER // 2}px", button.styleSheet())

    def test_the_palette_offers_the_shared_presets_plus_none(self):
        from ui.Pages.CurrentSheet import HIGHLIGHT_PALETTE, _COLOR_COLUMN

        page = self._page()
        actions = page.swatch_button(0).menu().actions()

        self.assertEqual(
            [action.text() for action in actions],
            [label for label, _color in HIGHLIGHT_PALETTE],
        )

    def test_choosing_a_palette_colour_highlights_the_whole_row(self):
        from ui.Pages.CurrentSheet import _COLOR_COLUMN

        page = self._page()
        # "Green" -- index 4 in HIGHLIGHT_PALETTE (None, Red, Orange, Yellow, Green...)
        page.swatch_button(0).menu().actions()[4].trigger()
        self._drain(800)  # let the ripple finish painting across the row

        self.assertEqual(storage_service.get_current_sheet_rows()[0]["row_color"], "#4CAF50")
        # tint_for is what the delegate paints from -- NOT item.background(),
        # which used to be asserted here and is why this test passed while
        # the row rendered untinted (see tests/test_current_sheet_render.py).
        for column in range(page.table.columnCount()):
            self.assertEqual(
                page.tint_for(0, column), "#4CAF50",
                msg=f"column {column} was left unhighlighted",
            )

    def test_choosing_none_clears_the_row(self):
        from ui.Pages.CurrentSheet import _COLOR_COLUMN

        page = self._page()
        actions = page.swatch_button(0).menu().actions()
        actions[4].trigger()   # Green
        actions[0].trigger()   # None

        self.assertIsNone(storage_service.get_current_sheet_rows()[0]["row_color"])

    def test_the_picker_column_is_not_treated_as_an_editable_cell(self):
        """It holds a button; a stray itemChanged on it must not try to
        save a value into a column that doesn't exist."""
        from ui.Pages.CurrentSheet import _COLOR_COLUMN

        page = self._page()
        before = storage_service.get_current_sheet_rows()[0]

        page._populating_table = False
        page.table.item(0, _COLOR_COLUMN).setText("junk")

        after = storage_service.get_current_sheet_rows()[0]
        self.assertEqual(before, after)

    def test_finalize_button_exists_and_shares_the_disable_state(self):
        page = self._page()

        self.assertTrue(page.finalize_btn.isEnabled())
        page._set_controls_enabled(False)
        self.assertFalse(page.update_btn.isEnabled())
        self.assertFalse(page.finalize_btn.isEnabled())

    def test_finalize_period_runs_from_the_last_export_to_today(self):
        from datetime import date

        page = self._page()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO app_state (key, value) VALUES ('last_export_date', '2026-06-30')"
        )
        conn.commit()
        conn.close()

        start, end = page._finalize_period()
        self.assertEqual(start, "2026-06-30")
        self.assertEqual(end, date.today().strftime("%Y-%m-%d"))

    def test_finalize_period_falls_back_to_this_month(self):
        from datetime import date

        page = self._page()
        start, end = page._finalize_period()

        today = date.today()
        self.assertEqual(start, today.replace(day=1).strftime("%Y-%m-%d"))
        self.assertEqual(end, today.strftime("%Y-%m-%d"))

    def test_setting_a_color_persists_and_paints_the_row(self):
        page = self._page()
        record = page._displayed_rows[0]

        page._set_color(0, record, "#4CAF50")
        self._drain(800)

        self.assertEqual(storage_service.get_current_sheet_rows()[0]["row_color"], "#4CAF50")
        self.assertEqual(page.tint_for(0, 0), "#4CAF50")

    def test_clearing_a_color_persists_and_unpaints_the_row(self):
        page = self._page()
        record = page._displayed_rows[0]
        page._set_color(0, record, "#4CAF50")
        self._drain(800)

        page._set_color(0, record, None)
        self._drain(800)

        self.assertIsNone(storage_service.get_current_sheet_rows()[0]["row_color"])
        # No tint means the delegate hands the cell back to the normal
        # theme styling rather than painting anything of its own.
        self.assertIsNone(page.tint_for(0, 0))

    def test_colouring_a_row_does_not_count_as_a_cell_edit(self):
        """Colouring must never be mistaken for a value edit -- the cell
        contents have to come through a recolour completely untouched."""
        page = self._page()
        before = storage_service.get_current_sheet_rows()[0]

        page._set_color(0, page._displayed_rows[0], "#E05252")

        after = storage_service.get_current_sheet_rows()[0]
        for column in ("day", "Date", "Project Name", "Task Name", "Qty"):
            self.assertEqual(before[column], after[column])


if __name__ == "__main__":
    unittest.main()

