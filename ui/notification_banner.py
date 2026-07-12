"""
Dismissible banner shown across the top of the content area when
Settings > Notifications is on and enough pending/rejected requests have
been sitting for longer than the configured threshold. MainWindow owns
the background timer that decides when to show/hide it (see ui/app.py).
"""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt, Signal

from ui.theme_utils import apply_live_style

WARNING_ICON = "⚠"


class NotificationBanner(QFrame):
    view_requested = Signal()
    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Scoped to "QFrame#notificationBanner" specifically -- an
        # unscoped setStyleSheet() on a widget with children cascades to
        # every descendant (including the "View" button), which is the
        # same bug that made Dashboard's stat card labels each render
        # inside their own little bordered box (see StatCard).
        self.setObjectName("notificationBanner")
        apply_live_style(self, lambda c: (
            f"QFrame#notificationBanner {{ background-color: {c['ACCENT_LIGHT']}; "
            f"border-bottom: 1px solid {c['ACCENT']}; }}"
        ))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 8, 20, 8)
        layout.setSpacing(10)

        icon_label = QLabel(WARNING_ICON)
        icon_label.setStyleSheet("background: transparent; font-size: 14px;")
        layout.addWidget(icon_label)

        self.message_label = QLabel("")
        apply_live_style(self.message_label, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 12px; background: transparent;")
        layout.addWidget(self.message_label, stretch=1)

        view_btn = QPushButton("View")
        view_btn.setObjectName("secondaryButton")
        view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_btn.clicked.connect(self.view_requested.emit)
        layout.addWidget(view_btn)

        dismiss_btn = QPushButton("✕")
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.setFixedSize(24, 24)
        apply_live_style(dismiss_btn, lambda c: f"""
            QPushButton {{ border: none; background: transparent; color: {c['TEXT_SECONDARY']}; font-size: 12px; }}
            QPushButton:hover {{ color: {c['TEXT_PRIMARY']}; }}
        """)
        dismiss_btn.clicked.connect(self._on_dismiss)
        layout.addWidget(dismiss_btn)

        self.hide()

    def _on_dismiss(self):
        self.hide()
        self.dismissed.emit()

    def show_message(self, text):
        self.message_label.setText(text)
        self.show()
