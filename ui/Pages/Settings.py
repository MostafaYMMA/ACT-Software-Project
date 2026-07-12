"""
Settings page. Currently just the light/dark toggle - more settings can
be added here later (per your "there are some other changes" note).
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import Qt

from ui.theme_manager import theme_manager
from ui.toggle_switch import ToggleSwitch, SUN_ICON, MOON_ICON


class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(10)

        self._icon_label = QLabel()
        self._icon_label.setStyleSheet("font-size: 15px;")
        row.addWidget(self._icon_label)

        self._mode_label = QLabel()
        self._mode_label.setStyleSheet("font-size: 13px;")
        row.addWidget(self._mode_label)

        row.addStretch()
        row.addWidget(ToggleSwitch())

        layout.addLayout(row)
        layout.addStretch()

        self._update_labels(theme_manager.mode)
        theme_manager.theme_changed.connect(self._update_labels)

    def _update_labels(self, mode):
        if mode == "dark":
            self._icon_label.setText(MOON_ICON)
            self._mode_label.setText("Dark mode")
        else:
            self._icon_label.setText(SUN_ICON)
            self._mode_label.setText("Light mode")