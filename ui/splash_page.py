"""
Reusable full-screen orange splash. Two uses in this app (see main.py):
  - Boot splash: title="OSMO", big font, shown at the very start of every
    launch while sync_cards runs in the background (spinner running).
  - Welcome splash: title="Welcome back", shown right after an account
    is selected/created, briefly, before entering the main app.

The spinner/message are NOT auto-started - the caller (main.py) decides
whether there's real work happening and calls start_loading()/
stop_loading() accordingly, so a page with nothing to actually load
doesn't show a spinner that isn't doing anything.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt

from ui.theme import COLOR_ACCENT, COLOR_TEXT_ON_ACCENT
from ui.loading_overlay import Spinner


class SplashPage(QWidget):
    def __init__(self, title, title_font_size=26, message=""):
        super().__init__()
        # Plain QWidget subclasses don't paint a QSS background-color
        # unless this attribute is set - without it, only child widgets
        # (like the title label) show color, not the widget itself.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLOR_ACCENT};")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(18)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {COLOR_TEXT_ON_ACCENT}; font-size: {title_font_size}px; font-weight: 800;"
        )
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        self.spinner = Spinner(self, size=38, color=COLOR_TEXT_ON_ACCENT)
        self.spinner.hide()
        layout.addWidget(self.spinner, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.message_label = QLabel(message)
        self.message_label.setStyleSheet(f"color: {COLOR_TEXT_ON_ACCENT}; font-size: 13px;")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setVisible(bool(message))
        layout.addWidget(self.message_label)

    def set_message(self, text):
        self.message_label.setText(text)
        self.message_label.setVisible(bool(text))

    def start_loading(self, message=None):
        """Call this when there's real background work happening."""
        if message:
            self.set_message(message)
        self.spinner.show()
        self.spinner.start()

    def stop_loading(self):
        self.spinner.stop()
        self.spinner.hide()
        self.set_message("")