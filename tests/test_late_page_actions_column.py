"""
The Late page's "Send Mail" button used to live in a dedicated, fixed-width
trailing "Actions" column so it never sat on top of real row data (it used
to be centered across the whole row, landing squarely on "Project Name").

That column is gone now. Hovering a row instead expands the row's own
height downward, opening a gap below its normal content just tall enough
for the button, centered across the FULL row width -- rows below shift
down to make room, and only one row is ever expanded at a time. See
ui/Pages/Late.py for the implementation notes, especially why moving the
cursor down into that gap must not be misread as leaving the row (it would
make the button disappear out from under the cursor trying to click it).
"""

import os
import sys
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _wait_for_animations(page, timeout=1.0):
    """Pumps the Qt event loop until both hover animations have settled
    (or `timeout` seconds pass, as a safety net against a stuck test)."""
    from PySide6.QtCore import QAbstractAnimation

    deadline = time.time() + timeout
    while time.time() < deadline:
        running = (
            page._expand_anim.state() == QAbstractAnimation.State.Running
            or page._collapse_anim.state() == QAbstractAnimation.State.Running
        )
        if not running:
            return
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        time.sleep(0.01)


class LatePageHoverExpandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _make_records(self, count):
        return [
            {
                "status": "Pending",
                "subject": f"Fwd: {i}",
                "Project Number": "555555555555",
                "Project Name": "HLGIU_NA_Marriott OPERA Cloud deployment",
                "Task Name": "Reporting - Reporting",
                "Date": "2026-06-01",
                "age_hours": 100,
                "sender": f"sender{i}@example.com",
            }
            for i in range(count)
        ]

    def setUp(self):
        import storage_service  # noqa: F401  (ensures services/ is importable first)

        self._records = self._make_records(5)
        self.get_stale_records_patcher = patch(
            "ui.Pages.Late.get_stale_records", return_value=self._records,
        )
        self.get_stale_records_patcher.start()
        self.addCleanup(self.get_stale_records_patcher.stop)

    def _build_page(self):
        from ui.Pages.Late import LatePage

        page = LatePage()
        page.resize(1400, 400)
        page.show()
        self.app.processEvents()
        return page

    # -- Part 1: no more Actions column ---------------------------------

    def test_no_dedicated_actions_column(self):
        from ui.Pages.Late import LatePage, _COLUMNS

        page = self._build_page()
        self.assertEqual(page.table.columnCount(), len(_COLUMNS))
        headers = [
            page.table.horizontalHeaderItem(i).text() for i in range(page.table.columnCount())
        ]
        self.assertNotIn("Actions", headers)

    # -- Expand/collapse behavior -----------------------------------------

    def test_hovering_a_row_expands_it_and_centers_the_button(self):
        page = self._build_page()
        normal_height = page._normal_row_height

        page._on_cell_entered(0, 3)
        _wait_for_animations(page)

        expanded_height = normal_height + page.send_button.height() + 2 * 10
        self.assertEqual(page.table.rowHeight(0), expanded_height)
        self.assertTrue(page.send_button.isVisible())

        viewport_width = page.table.viewport().width()
        expected_x = (viewport_width - page.send_button.width()) // 2
        self.assertLessEqual(abs(page.send_button.pos().x() - expected_x), 1)

        row_top = page.table.rowViewportPosition(0)
        btn_y = page.send_button.pos().y()
        self.assertGreaterEqual(btn_y, row_top + normal_height)
        self.assertLessEqual(btn_y + page.send_button.height(), row_top + expanded_height)

    def test_hovering_a_new_row_collapses_the_previous_one(self):
        page = self._build_page()
        normal_height = page._normal_row_height

        page._on_cell_entered(0, 3)
        _wait_for_animations(page)
        self.assertEqual(page._expanded_row, 0)

        page._on_cell_entered(4, 3)
        _wait_for_animations(page)

        self.assertEqual(page.table.rowHeight(0), normal_height)
        self.assertEqual(page._expanded_row, 4)
        self.assertGreater(page.table.rowHeight(4), normal_height)

    def test_rapid_hover_across_many_rows_leaves_only_the_last_expanded(self):
        page = self._build_page()
        normal_height = page._normal_row_height

        for row in range(5):
            page._on_cell_entered(row, 3)
        _wait_for_animations(page)

        for row in range(4):
            self.assertEqual(page.table.rowHeight(row), normal_height, f"row {row} should be collapsed")
        self.assertGreater(page.table.rowHeight(4), normal_height)
        self.assertEqual(page._expanded_row, 4)

    def test_leaving_the_viewport_collapses_the_expanded_row(self):
        from PySide6.QtCore import QEvent, QPoint

        page = self._build_page()
        normal_height = page._normal_row_height

        page._on_cell_entered(0, 3)
        _wait_for_animations(page)
        self.assertEqual(page._expanded_row, 0)

        # Cursor is genuinely outside the viewport -- must collapse.
        with patch("ui.Pages.Late.QCursor") as mock_cursor:
            mock_cursor.pos.return_value = QPoint(-500, -500)
            page.eventFilter(page.table.viewport(), QEvent(QEvent.Type.Leave))
        _wait_for_animations(page)

        self.assertIsNone(page._expanded_row)
        self.assertFalse(page.send_button.isVisible())
        self.assertEqual(page.table.rowHeight(0), normal_height)

    # -- Edge case: cursor moving down into the gap must not drop the hover --

    def test_moving_into_the_gap_keeps_the_row_expanded_and_the_button_live(self):
        """Directly reproduces the scenario the row-expand approach exists
        for: the cursor moves DOWN off the original cell content and into
        the newly opened gap where the button sits. Two things must both
        hold: (a) Qt's own hit-testing for a point inside that gap still
        resolves to the expanded row (proving a real cellEntered there
        would be a no-op, not a collapse), and (b) a Leave event fired
        while the cursor is positioned over the button itself (still
        geometrically inside the viewport) must NOT collapse the row --
        otherwise the button would vanish out from under a cursor trying
        to click it."""
        from PySide6.QtCore import QEvent, QPoint

        page = self._build_page()
        page._on_cell_entered(0, 3)
        _wait_for_animations(page)
        self.assertEqual(page._expanded_row, 0)

        row_top = page.table.rowViewportPosition(0)
        row_height = page.table.rowHeight(0)
        gap_point = QPoint(page.table.viewport().width() // 2, row_top + row_height - 5)

        hit_item = page.table.itemAt(gap_point)
        self.assertIsNotNone(hit_item, "a point inside the expanded gap should still hit row 0's cells")
        self.assertEqual(hit_item.row(), 0)

        # Simulate the Leave Qt delivers to the viewport when the cursor
        # moves onto the button child widget -- cursor is still logically
        # within the viewport's own rect, just occluded by the button.
        button_global_pos = page.send_button.mapToGlobal(QPoint(
            page.send_button.width() // 2, page.send_button.height() // 2
        ))
        with patch("ui.Pages.Late.QCursor") as mock_cursor:
            mock_cursor.pos.return_value = button_global_pos
            page.eventFilter(page.table.viewport(), QEvent(QEvent.Type.Leave))

        self.assertEqual(page._expanded_row, 0, "row must stay expanded while cursor is over the button")
        self.assertTrue(page.send_button.isVisible())

        # And the button must actually still be clickable end to end.
        with patch("ui.Pages.Late.QDesktopServices.openUrl", return_value=True) as mock_open:
            page._on_send_mail_clicked()
        mock_open.assert_called_once()
        self.assertIn("sender0%40example.com", mock_open.call_args.args[0].toString())

    def test_refresh_resets_all_row_heights_and_hides_the_button(self):
        page = self._build_page()
        normal_height = page._normal_row_height

        page._on_cell_entered(0, 3)
        _wait_for_animations(page)
        self.assertTrue(page.send_button.isVisible())

        page.refresh()

        self.assertIsNone(page._expanded_row)
        self.assertFalse(page.send_button.isVisible())
        for row in range(page.table.rowCount()):
            self.assertEqual(page.table.rowHeight(row), normal_height)

    def test_scrolled_out_of_view_hides_the_button_instead_of_floating(self):
        page = self._build_page()
        page._on_cell_entered(0, 3)
        _wait_for_animations(page)
        self.assertTrue(page.send_button.isVisible())

        with patch.object(page.table, "rowViewportPosition", return_value=-10_000):
            page._reposition_button_for_expanded_row()

        self.assertFalse(page.send_button.isVisible())

    def test_click_behavior_opens_mailto_for_the_hovered_row(self):
        page = self._build_page()
        page._on_cell_entered(0, 3)
        _wait_for_animations(page)

        with patch("ui.Pages.Late.QDesktopServices.openUrl", return_value=True) as mock_open:
            page._on_send_mail_clicked()

        mock_open.assert_called_once()
        url = mock_open.call_args.args[0]
        self.assertIn("sender0%40example.com", url.toString())


if __name__ == "__main__":
    unittest.main()
