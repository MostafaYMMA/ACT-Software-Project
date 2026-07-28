"""
Covers ui/select_account_page.py's SelectAccountPage: every existing
account gets a tile, plus an "Add account" tile, and clicking an
account tile requires re-entering that account's password before
account_selected fires.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ui.athu as athu


class SelectAccountPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        config_dir = os.path.join(self.temp_dir.name, ".timecard_app")
        os.makedirs(config_dir, exist_ok=True)
        accounts_file = os.path.join(config_dir, "accounts.json")

        self.config_patcher = patch.object(athu, "CONFIG_DIR", config_dir)
        self.file_patcher = patch.object(athu, "ACCOUNTS_FILE", accounts_file)
        self.config_patcher.start()
        self.file_patcher.start()
        self.addCleanup(self.config_patcher.stop)
        self.addCleanup(self.file_patcher.stop)

    def _tile_labels(self, page):
        from ui.select_account_page import AccountTile

        return [t for t in page._grid_container.findChildren(AccountTile)]

    def test_add_tile_shown_when_there_are_zero_accounts(self):
        from ui.select_account_page import SelectAccountPage

        page = SelectAccountPage()
        page._rebuild_grid()
        tiles = self._tile_labels(page)
        self.assertEqual(len(tiles), 1)
        self.assertTrue(tiles[0].is_add_tile)
        page.deleteLater()

    def test_add_tile_still_shown_alongside_an_existing_account(self):
        from ui.select_account_page import SelectAccountPage

        athu.save_account("Omar", "secret123")
        page = SelectAccountPage()
        page._rebuild_grid()
        tiles = self._tile_labels(page)
        self.assertEqual(sorted(t.username for t in tiles if not t.is_add_tile), ["Omar"])
        self.assertTrue(any(t.is_add_tile for t in tiles))
        page.deleteLater()

    def test_clicking_a_tile_requires_the_correct_password_before_selecting(self):
        from ui.select_account_page import SelectAccountPage

        athu.save_account("Omar", "secret123")
        page = SelectAccountPage()
        page._rebuild_grid()

        selected = []
        page.account_selected.connect(selected.append)

        page._on_tile_clicked("Omar")
        self.assertTrue(page._password_card.isVisible() or not page._password_card.isHidden())

        page._password_input._input.setText("wrong-password")
        page._confirm_password()
        self.assertFalse(selected, "wrong password must not select the account")
        self.assertTrue(page._password_error.text())

        page._password_input._input.setText("secret123")
        page._confirm_password()
        self.assertEqual(selected, ["Omar"])
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
