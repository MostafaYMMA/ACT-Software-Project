"""
Pixel-level tests for the Current Sheet row highlight.

These exist because the previous tests asserted
QTableWidgetItem.background(), which is the brush STORED on the item --
not what reaches the screen. ui/theme.py styles `QTableWidget::item`, and
once any stylesheet rule targets items Qt's QStyleSheetStyle paints the
cell background itself and ignores that brush. So the old tests passed
green while the feature painted nothing: the only coloured thing on screen
was the swatch button, a real widget drawn on top of its cell.

Every test here therefore renders the table and reads actual pixels, with
the app stylesheet applied exactly as the running app applies it. Without
that stylesheet these tests would pass even against the broken version.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage_service

GREEN = "#4CAF50"
YELLOW = "#FFEE33"


class CurrentSheetRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        from ui.theme_manager import theme_manager

        cls.app = QApplication.instance() or QApplication([])
        # THE point of these tests -- see the module docstring.
        cls.app.setStyleSheet(theme_manager.stylesheet())

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "cards_test.db")

        patcher = patch.object(storage_service, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        storage_service.init_db()
        conn = sqlite3.connect(self.db_path)
        for index in range(3):
            conn.execute(
                'INSERT INTO current_sheet (timecard_id, day, "Date", "Project Number", '
                '"Project Name", "Task Name", "Qty") VALUES (?, ?, ?, ?, ?, ?, ?)',
                (index, "Monday", f"2026-07-0{index + 1}", "P1", "FB Kitchen", "T1", "8"),
            )
        conn.commit()
        conn.close()

        from ui.Pages.CurrentSheet import CurrentSheetPage

        self.page = CurrentSheetPage()
        self.page.resize(1100, 400)
        self.page.show()
        self._drain(200)

    def _drain(self, ms):
        from PySide6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def _row_backgrounds(self, row_index):
        """The painted background colour of every DATA cell in a row.

        Sampled a few pixels in from the left edge, not the centre: the
        text is centre-aligned, so a centre sample reads the glyph colour
        instead of the background. The swatch column is skipped -- it holds
        a QToolButton, which is a widget rather than a painted cell."""
        from PySide6.QtGui import QPixmap

        pixmap = QPixmap(self.page.table.viewport().size())
        self.page.table.viewport().render(pixmap)
        image = pixmap.toImage()

        colours = []
        for column in range(1, self.page.table.columnCount()):
            rect = self.page.table.visualItemRect(self.page.table.item(row_index, column))
            x, y = rect.left() + 3, rect.center().y()
            if 0 <= x < image.width() and 0 <= y < image.height():
                colours.append(image.pixelColor(x, y).name().upper())
        return colours

    def _pick(self, row_index, palette_index):
        from ui.Pages.CurrentSheet import _COLOR_COLUMN

        self.page.swatch_button(row_index).menu().actions()[palette_index].trigger()
        self._drain(900)  # let the ripple finish crossing the row

    # -----------------------------------------------------------------
    def test_picking_a_colour_tints_every_cell_in_the_row(self):
        """The regression this whole file exists for: it used to tint none
        of them, with only the swatch widget showing the colour."""
        self._pick(0, 4)  # Green

        colours = self._row_backgrounds(0)
        self.assertTrue(colours, "no data cells were sampled")
        self.assertEqual(
            set(colours), {GREEN},
            msg=f"expected every cell tinted {GREEN}, got {colours}",
        )

    def test_other_rows_are_left_alone(self):
        self._pick(0, 4)

        for row_index in (1, 2):
            self.assertNotIn(GREEN, self._row_backgrounds(row_index))

    def test_clearing_reverts_the_row_to_default_styling(self):
        self._pick(0, 4)
        untouched = self._row_backgrounds(1)

        self._pick(0, 0)  # None

        self.assertNotIn(GREEN, self._row_backgrounds(0))
        self.assertEqual(
            self._row_backgrounds(0), untouched,
            msg="a cleared row should render identically to one never coloured",
        )

    def test_a_selected_tinted_row_shows_both_states(self):
        """Layered, not overridden: the tint must still be recognisable,
        and the row must still look different from an unselected one."""
        self._pick(0, 4)
        unselected = self._row_backgrounds(0)

        self.page.table.selectRow(0)
        self._drain(200)
        selected = self._row_backgrounds(0)

        self.assertNotEqual(selected, unselected, "selection is not visible at all")
        self.assertNotIn(GREEN, selected, "selection did not affect the tint")
        # Still green-dominant -- i.e. blended toward the accent, not replaced.
        from PySide6.QtGui import QColor

        colour = QColor(selected[0])
        self.assertGreater(
            colour.green(), max(colour.red(), colour.blue()),
            msg=f"the tint was replaced rather than blended ({selected[0]})",
        )

    def test_hover_darkens_the_tint(self):
        self._pick(0, 4)
        resting = self._row_backgrounds(0)

        self.page._set_hovered_row(0)
        self._drain(150)
        hovered = self._row_backgrounds(0)

        self.assertNotEqual(hovered, resting)
        from PySide6.QtGui import QColor

        self.assertLess(
            QColor(hovered[0]).lightness(), QColor(resting[0]).lightness(),
            msg="hover should darken the tint, not lighten it",
        )

    def test_text_colour_flips_for_a_light_tint(self):
        """#FFEE33 is far too light for white text; #4CAF50 is dark enough
        that black wins. The choice is per-colour by measured WCAG
        contrast, so both have to come out legible."""
        from ui.Pages.CurrentSheet import readable_text_color
        from PySide6.QtGui import QColor

        self.assertEqual(readable_text_color(QColor(YELLOW)), "#000000")
        self.assertEqual(readable_text_color(QColor("#1A1A1A")), "#FFFFFF")

    def test_every_palette_colour_meets_wcag_body_contrast(self):
        from ui.Pages.CurrentSheet import (
            HIGHLIGHT_PALETTE, readable_text_color, _relative_luminance, _contrast_ratio,
        )
        from PySide6.QtGui import QColor

        for label, hex_color in HIGHLIGHT_PALETTE:
            if not hex_color:
                continue
            with self.subTest(colour=label):
                background = QColor(hex_color)
                text = QColor(readable_text_color(background))
                ratio = _contrast_ratio(
                    _relative_luminance(background), _relative_luminance(text)
                )
                self.assertGreaterEqual(
                    ratio, 4.5,
                    msg=f"{label} ({hex_color}) only reaches {ratio:.2f}:1",
                )

    def test_the_tint_survives_a_refresh(self):
        """It's stored on the record, not in the painting -- so re-rendering
        the table (a scan, a page revisit) must not lose it."""
        self._pick(0, 4)

        self.page.refresh()
        self._drain(500)

        self.assertEqual(set(self._row_backgrounds(0)), {GREEN})

    def test_the_ripple_crosses_the_row_progressively(self):
        """Keeping the ripple was a deliberate choice -- this pins that it
        is still a wash rather than an instant fill, and that it completes."""
        from ui.Pages.CurrentSheet import _COLOR_COLUMN

        self.page.swatch_button(0).menu().actions()[4].trigger()
        self._drain(40)  # deliberately mid-wash

        mid = self._row_backgrounds(0)
        self.assertIn(GREEN, mid, "the wash never started")
        self.assertGreater(
            len(set(mid)), 1, "the row filled instantly instead of washing across",
        )

        self._drain(900)
        self.assertEqual(set(self._row_backgrounds(0)), {GREEN}, "the wash did not finish")

    def test_a_second_pick_mid_ripple_still_lands_correctly(self):
        """Cancelling a wash must leave the row on its final colour, not
        stranded half-way between two."""
        from ui.Pages.CurrentSheet import _COLOR_COLUMN

        menu = self.page.swatch_button(0).menu()
        menu.actions()[4].trigger()   # Green
        self._drain(30)
        menu.actions()[3].trigger()   # Yellow, interrupting
        self._drain(900)

        self.assertEqual(set(self._row_backgrounds(0)), {YELLOW})
        self.assertEqual(storage_service.get_current_sheet_rows()[0]["row_color"], YELLOW)


if __name__ == "__main__":
    unittest.main()

