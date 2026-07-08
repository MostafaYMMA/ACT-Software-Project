"""
Entry point. Decides which screen to show first:
  - No accounts on this machine yet  -> AccountCreationPage
  - One or more accounts exist       -> SelectAccountPage (tap to log in)
Then hands off to MainWindow once a user is chosen/created.
This file is the only "traffic cop" - it doesn't contain any account
logic (that's ui/athu.py) or any page layout (that's the rest of ui/).
"""
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "services"))

from PySide6.QtWidgets import QApplication, QMainWindow

from ui.athu import accounts_exist
from ui.account_page import AccountCreationPage
from ui.select_account_page import SelectAccountPage
from ui.app import MainWindow
from ui.theme import GLOBAL_STYLESHEET
from ui.transition import FadeStackedWidget

from filter_service import get_approved_cards
from extractor_service import extract
from storage_service import init_db, save_cards, export_to_csv


def sync_cards():
    """Pull approved timecard emails, extract entries, and persist them."""
    init_db()

    emails = get_approved_cards()
    print(f"\nApproved emails found: {len(emails)}")

    all_entries = []
    for email in emails:
        entries = extract(email)
        print(f"  - '{email.Subject}' -> {len(entries)} entries")
        all_entries.extend(entries)

    print(f"\nTotal entries extracted: {len(all_entries)}")

    if all_entries:
        save_cards(all_entries)
        print("Saved entries to database.")
        export_to_csv()
    else:
        print("Nothing to save.")


class RootWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Timecard app")
        self.resize(1050, 650)

        self.stack = FadeStackedWidget()
        self.setCentralWidget(self.stack)

        self.account_page = AccountCreationPage()
        self.account_page.account_created.connect(self._on_account_created)
        self.stack.addWidget(self.account_page)

        self.select_page = SelectAccountPage()
        self.select_page.account_selected.connect(self._on_account_selected)
        self.select_page.add_account_requested.connect(self._show_account_creation)
        self.stack.addWidget(self.select_page)

        if accounts_exist():
            self.stack.setCurrentWidget(self.select_page)
        else:
            self.stack.setCurrentWidget(self.account_page)

    def _show_account_creation(self):
        self.stack.setCurrentWidget(self.account_page)

    def _on_account_created(self, username):
        # First-time creation (or "Add account") -> log straight into the app.
        self._enter_main_app(username)

    def _on_account_selected(self, username):
        # Tapped a tile on the select-account screen -> log straight in.
        self._enter_main_app(username)

    def _enter_main_app(self, username):
        main_widget = MainWindow(username)
        self.stack.addWidget(main_widget)
        self.stack.setCurrentWidget(main_widget)


if __name__ == "__main__":
    sync_cards()

    app = QApplication(sys.argv)
    app.setStyleSheet(GLOBAL_STYLESHEET)
    window = RootWindow()
    window.show()
    sys.exit(app.exec())