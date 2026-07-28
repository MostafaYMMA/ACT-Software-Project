"""
Covers main.py::RootWindow's account-startup routing: no accounts ->
AccountCreationPage, one or more accounts -> SelectAccountPage (which
itself is responsible for resolving "which account" via a password-
gated tile click - see ui/select_account_page.py and
test_select_account_page.py for that page's own behavior).

"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage_service
from schemas.accounts import Account


class StartupRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        db_path = os.path.join(self.temp_dir.name, "cards.db")
        self.db_patcher = patch.object(storage_service, "DB_PATH", db_path)
        self.db_patcher.start()
        self.addCleanup(self.db_patcher.stop)

    def _make_account(self, username):
        return Account(username=username, salt="s", password_hash="h")

    def _build_root_window(self, accounts):
        import main

        window = main.RootWindow()
        with patch.object(main, "accounts_exist", return_value=bool(accounts)):
            window._on_sync_finished()
        return window

    def test_zero_accounts_routes_to_account_creation(self):
        window = self._build_root_window([])
        self.assertIs(window.stack.currentWidget(), window.account_page)
        window.deleteLater()

    def test_one_account_routes_to_select_page(self):
        window = self._build_root_window([self._make_account("Omar")])
        self.assertIs(window.stack.currentWidget(), window.select_page)
        window.deleteLater()

    def test_multiple_accounts_also_route_to_select_page(self):
        # select_account_page itself is what resolves "more than one
        # account" -- every tile shown, password required to pick one --
        # rather than this file guessing or discarding data.
        window = self._build_root_window(
            [self._make_account("Omar"), self._make_account("Seif")]
        )
        self.assertIs(window.stack.currentWidget(), window.select_page)
        window.deleteLater()

    def test_switch_account_button_emits_its_signal(self):
        from ui.app import MainWindow

        storage_service.init_db()  # DB_PATH is patched to a temp file in setUp
        main_window = MainWindow("Omar")
        from PySide6.QtWidgets import QPushButton
        switch_btn = next(
            b for b in main_window.findChildren(QPushButton) if "Switch Account" in b.text()
        )
        received = []
        main_window.switch_account_requested.connect(lambda: received.append(True))
        switch_btn.click()
        self.assertTrue(received, "clicking the button should emit the signal")
        main_window.deleteLater()


if __name__ == "__main__":
    unittest.main()
