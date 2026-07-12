"""
Entry point. Flow on every launch:
  1. Boot splash ("OSMO", big white text on orange) - shown immediately,
     while sync_cards runs in the background.
  2. Once sync finishes:
       - No accounts yet on this machine -> AccountCreationPage
       - One or more accounts exist      -> SelectAccountPage
  3. Once an account is created OR selected -> brief "Welcome back"
     splash -> MainWindow (the actual dashboard/sidebar app).

This file is the only "traffic cop" - it doesn't contain any account
logic (that's ui/athu.py) or any page layout (that's the rest of ui/).
"""
import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.append(os.path.join(os.path.dirname(__file__), "services"))

from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import QThread, QObject, Signal, QTimer

from ui.athu import accounts_exist
from ui.account_page import AccountCreationPage
from ui.select_account_page import SelectAccountPage
from ui.splash_page import SplashPage
from ui.app import MainWindow
from ui.theme_manager import theme_manager
from ui.transition import FadeStackedWidget, zoom_in

from filter_service import get_approved_cards
from extractor_service import extract
from storage_service import init_db, save_cards, export_to_csv, export_invoice_lines_to_excel

WELCOME_SPLASH_DURATION_MS = 900


def sync_cards(progress_callback=None):
    """Pull approved timecard emails, extract entries, and persist them.
    progress_callback, if given, is called with short status strings so
    the boot splash can show what's currently happening."""
    def report(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    init_db()

    report("Checking inbox for approved timecards...")
    emails = get_approved_cards(limit=100)
    report(f"Approved emails found: {len(emails)}")

    all_entries = []
    for email in emails:
        entries = extract(email)
        print(f"  - '{email['subject']}' -> {len(entries)} entries")
        all_entries.extend(entries)

    report(f"Total entries extracted: {len(all_entries)}")

    if all_entries:
        report("Saving entries...")
        save_cards(all_entries)
        print("Saved entries to database.")
        export_to_csv()
        export_invoice_lines_to_excel()
    else:
        print("Nothing to save.")


class SyncWorker(QObject):
    """Runs sync_cards() on a background thread so the boot splash's
    spinner can keep animating and its message can update while it runs,
    instead of the whole app freezing until it's done."""
    progress = Signal(str)
    finished = Signal()

    def run(self):
        sync_cards(progress_callback=self.progress.emit)
        self.finished.emit()


class RootWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Timecard app")
        self.resize(1050, 650)

        self.stack = FadeStackedWidget()
        self.setCentralWidget(self.stack)

        # Boot splash - shown first, every launch, no exceptions.
        self.boot_splash = SplashPage(title="OSMO", title_font_size=64)
        self.stack.addWidget(self.boot_splash)

        # Welcome splash - shown right after an account is picked/created.
        self.welcome_splash = SplashPage(title="Welcome back", title_font_size=26)
        self.stack.addWidget(self.welcome_splash)

        self.account_page = AccountCreationPage()
        self.account_page.account_created.connect(self._on_account_created)
        self.stack.addWidget(self.account_page)

        self.select_page = SelectAccountPage()
        self.select_page.account_selected.connect(self._on_account_selected)
        self.select_page.add_account_requested.connect(self._show_account_creation)
        self.stack.addWidget(self.select_page)

        self.stack.setCurrentWidget(self.boot_splash)
        self.boot_splash.start_loading("Loading...")
        self._start_background_sync()

    def _start_background_sync(self):
        self._sync_thread = QThread(self)
        self._sync_worker = SyncWorker()
        self._sync_worker.moveToThread(self._sync_thread)

        self._sync_thread.started.connect(self._sync_worker.run)
        self._sync_worker.progress.connect(self.boot_splash.set_message)
        self._sync_worker.finished.connect(self._on_sync_finished)
        self._sync_worker.finished.connect(self._sync_thread.quit)

        self._sync_thread.start()

    def _on_sync_finished(self):
        self.boot_splash.stop_loading()
        if accounts_exist():
            self.stack.setCurrentWidget(self.select_page)
        else:
            self.stack.setCurrentWidget(self.account_page)

    def _show_account_creation(self):
        self.stack.setCurrentWidget(self.account_page)

    def _on_account_created(self, username):
        self._show_welcome_then_enter(username)

    def _on_account_selected(self, username):
        self._show_welcome_then_enter(username)

    def _show_welcome_then_enter(self, username):
        self.stack.setCurrentWidget(self.welcome_splash)
        QTimer.singleShot(WELCOME_SPLASH_DURATION_MS, lambda: self._enter_main_app(username))

    def _enter_main_app(self, username):
        main_widget = MainWindow(username)
        self.stack.addWidget(main_widget)
        self.stack.setCurrentWidget(main_widget)  # fade (from FadeStackedWidget)
        zoom_in(main_widget)  # layered on top, specifically for "arriving" into the account


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Apply your global stylesheet
    app.setStyleSheet(theme_manager.stylesheet())

    # Create the main window
    window = RootWindow()

    # Open the window maximized
    window.showMaximized()

    # Start the application
    sys.exit(app.exec())