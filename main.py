"""
Entry point. Flow on every launch:
  1. Boot splash (animated ACT logo) - shown immediately.
  2. The app then routes to account creation or account selection:
       - No accounts yet on this machine -> AccountCreationPage
       - One or more accounts exist      -> SelectAccountPage
  3. Once an account is created OR selected -> straight into
     MainWindow (the actual dashboard/sidebar app) - no intermediate
     "Welcome back" splash step anymore.

This file is the only "traffic cop" - it doesn't contain any account
logic (that's ui/athu.py) or any page layout (that's the rest of ui/).

Added: a small floating-logo slide animation, played at two points:
  - boot splash -> select-account page (logo shrinks and glides
    slightly upward, staying centered, landing on
    select_page.page_logo_label)
  - account created/selected -> main window (logo glides into the top
    bar, landing on main_widget.topbar_logo_label)

Both reuse the same _play_logo_slide helper: a floating copy of the
boot logo animates from its resting boot position to whatever target
label is passed in, then hides right as the real static logo is
already sitting there - so it reads as one continuous motion instead
of pages just swapping. Purely additive - no existing routing, timers,
or signals are changed.
"""
from PySide6.QtCore import QTimer
import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.append(os.path.join(os.path.dirname(__file__), "services"))

from PySide6.QtWidgets import QApplication, QMainWindow, QLabel
from PySide6.QtCore import QTimer, QPoint, QRect, QPropertyAnimation, QEasingCurve

from ui.athu import accounts_exist
from ui.account_page import AccountCreationPage
from ui.select_account_page import SelectAccountPage
from ui.boot_logo_splash import BootLogoSplash
from ui.app import MainWindow
from ui.theme_manager import theme_manager
from ui.transition import FadeStackedWidget, zoom_in
from storage_service import init_db


WELCOME_SPLASH_DURATION_MS = 900

LOGO_SLIDE_ANIM_MS = 550


class RootWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Must happen before ANY page gets built (Dashboard/History/Records
        # /the notification banner all query the DB directly) -- previously
        # this only ran inside sync_cards(), i.e. after the user clicked
        # "Scan Inbox", so a fresh/deleted database file crashed the app on
        # launch the moment a page queried a table that didn't exist yet.
        init_db()

        self.setWindowTitle("Timecard app")
        self.resize(1050, 650)

        self.stack = FadeStackedWidget()
        self.setCentralWidget(self.stack)

        # Boot splash - shown first, every launch, no exceptions.
        self.boot_splash = BootLogoSplash()
        self.stack.addWidget(self.boot_splash)

        self.account_page = AccountCreationPage()
        self.account_page.account_created.connect(self._on_account_created)
        self.account_page.back_requested.connect(self._show_select_page)
        self.stack.addWidget(self.account_page)

        self.select_page = SelectAccountPage()
        self.select_page.account_selected.connect(self._on_account_selected)
        self.select_page.add_account_requested.connect(self._show_account_creation)
        self.stack.addWidget(self.select_page)

        self._current_main_widget = None

        # Floating logo shared by both slide animations below. Parented
        # directly on RootWindow (not the stack) so it can sit on top of
        # everything and move freely regardless of which stacked page is
        # current.
        self._handoff_logo = QLabel(self)
        self._handoff_logo.setScaledContents(True)
        self._handoff_logo.hide()
        self._handoff_anim = None

        self.stack.setCurrentWidget(self.boot_splash)
        self.boot_splash.start_loading("Loading...")
        QTimer.singleShot(2000, self._on_sync_finished)

    def _on_sync_finished(self):
        self.boot_splash.stop_loading()
        if accounts_exist():
            self.stack.setCurrentWidget(self.select_page)  # crossfade (FadeStackedWidget)
            QTimer.singleShot(0, lambda: self._play_logo_slide(
                self.boot_splash.logo_label,
                getattr(self.select_page, "page_logo_label", None),
            ))
        else:
            self.stack.setCurrentWidget(self.account_page)

    def _show_account_creation(self):
        self.stack.setCurrentWidget(self.account_page)

    def _show_select_page(self):
        self.stack.setCurrentWidget(self.select_page)

    def _on_account_created(self, username):
        self._enter_main_app(username)

    def _on_account_selected(self, username):
        self._enter_main_app(username)

    def _enter_main_app(self, username):
        main_widget = MainWindow(username)
        main_widget.switch_account_requested.connect(self._on_switch_account_requested)
        self.stack.addWidget(main_widget)
        self.stack.setCurrentWidget(main_widget)  # fade (from FadeStackedWidget)
        zoom_in(main_widget)  # layered on top, specifically for "arriving" into the account
        self._current_main_widget = main_widget

        # Kick off the logo slide right as the main window arrives.
        # Deferred one tick so main_widget's top bar has a settled
        # layout/geometry to animate into.
        QTimer.singleShot(0, lambda: self._play_logo_slide(
            self.boot_splash.logo_label,
            getattr(main_widget, "topbar_logo_label", None),
        ))

    def _play_logo_slide(self, source_label, target_label):
        """Animates a floating copy of source_label's pixmap sliding
        (position + size) into target_label's on-screen spot, then hides
        it - timed so the real static logo at the target is already in
        place underneath by the time it disappears."""
        source_pixmap = source_label.pixmap() if source_label is not None else None
        if source_pixmap is None or source_pixmap.isNull() or target_label is None:
            return  # nothing sane to animate - real logo is already in place either way

        start_rect = QRect(self.mapFromGlobal(source_label.mapToGlobal(QPoint(0, 0))), source_label.size())
        end_rect = QRect(self.mapFromGlobal(target_label.mapToGlobal(QPoint(0, 0))), target_label.size())

        self._handoff_logo.setPixmap(source_pixmap)
        self._handoff_logo.setGeometry(start_rect)
        self._handoff_logo.show()
        self._handoff_logo.raise_()

        anim = QPropertyAnimation(self._handoff_logo, b"geometry", self)
        anim.setDuration(LOGO_SLIDE_ANIM_MS)
        anim.setStartValue(start_rect)
        anim.setEndValue(end_rect)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._handoff_logo.hide)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._handoff_anim = anim  # prevent garbage collection mid-animation

    def _on_switch_account_requested(self):
        self.stack.setCurrentWidget(self.select_page)
        if self._current_main_widget is not None:
            self.stack.removeWidget(self._current_main_widget)
            self._current_main_widget.deleteLater()
            self._current_main_widget = None


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