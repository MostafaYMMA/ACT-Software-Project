"""
The Late page's "Send Mail" button used to be centered across the WHOLE
row viewport width, which for this 7-column table landed it squarely on
top of the "Project Name" column, hiding that row's actual value.

It now lives in a dedicated, fixed-width trailing "Actions" column, so it
never sits on top of real data. This is a layout-only fix -- the click
behavior (opening a mailto: compose window addressed to the row's sender)
is unchanged and is covered here too, to prove the move didn't disturb it.
"""

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class LatePageActionsColumnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import storage_service

        self.get_stale_records_patcher = patch(
            "ui.Pages.Late.get_stale_records",
            return_value=[{
                "status": "Pending",
                "subject": "Fwd: 789",
                "Project Number": "555555555555",
                "Project Name": "HLGIU_NA_Marriott OPERA Cloud deployment",
                "Task Name": "Reporting - Reporting",
                "Date": "2026-06-01",
                "age_hours": 100,
                "sender": "sender@example.com",
            }],
        )
        self.get_stale_records_patcher.start()
        self.addCleanup(self.get_stale_records_patcher.stop)

    def test_actions_is_a_dedicated_trailing_column(self):
        from ui.Pages.Late import LatePage, _COLUMNS, _ACTIONS_COLUMN_INDEX

        page = LatePage()
        self.assertEqual(page.table.columnCount(), len(_COLUMNS) + 1)
        self.assertEqual(_ACTIONS_COLUMN_INDEX, len(_COLUMNS))
        self.assertEqual(page.table.horizontalHeaderItem(_ACTIONS_COLUMN_INDEX).text(), "Actions")

    def test_project_name_cell_is_not_covered_by_the_send_button(self):
        from ui.Pages.Late import LatePage, _ACTIONS_COLUMN_INDEX

        page = LatePage()
        page.resize(1400, 300)
        page.show()
        self.app.processEvents()

        project_name_col = 3  # Status, Subject, Project Number, Project Name, ...
        self.assertEqual(
            page.table.horizontalHeaderItem(project_name_col).text(), "Project Name",
        )

        page._on_cell_entered(0, project_name_col)
        self.app.processEvents()

        proj_x = page.table.columnViewportPosition(project_name_col)
        proj_w = page.table.columnWidth(project_name_col)
        btn_x = page.send_button.pos().x()
        btn_right = btn_x + page.send_button.width()

        overlaps = not (btn_right <= proj_x or btn_x >= proj_x + proj_w)
        self.assertFalse(overlaps, "Send Mail button must not overlap the Project Name column")

        actions_x = page.table.columnViewportPosition(_ACTIONS_COLUMN_INDEX)
        self.assertGreaterEqual(btn_x, actions_x, "Send Mail button must sit within the Actions column")

    def test_project_name_value_is_still_present_and_readable_in_its_own_cell(self):
        from ui.Pages.Late import LatePage

        page = LatePage()
        project_name_col = 3
        item = page.table.item(0, project_name_col)
        self.assertEqual(item.text(), "HLGIU_NA_Marriott OPERA Cloud deployment")

    def test_click_behavior_is_unchanged_opens_mailto_for_the_hovered_row(self):
        from ui.Pages.Late import LatePage

        page = LatePage()
        page.resize(1400, 300)
        page.show()
        self.app.processEvents()
        page._on_cell_entered(0, 3)

        with patch("ui.Pages.Late.QDesktopServices.openUrl", return_value=True) as mock_open:
            page._on_send_mail_clicked()

        mock_open.assert_called_once()
        url = mock_open.call_args.args[0]
        self.assertIn("sender%40example.com", url.toString())


if __name__ == "__main__":
    unittest.main()
