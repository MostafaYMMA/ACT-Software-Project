"""
Settings page: light/dark toggle, plus the stale-threshold controls
(enable/disable + how many hours old counts as "late", both persisted
via ui/notification_settings.py). That threshold drives both the
banner in ui/app.py and the Late tab (ui/Pages/Late.py).
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QComboBox, QFrame
from PySide6.QtCore import Qt

from ui.theme_manager import theme_manager
from ui.theme_utils import apply_live_style
from ui.toggle_switch import ToggleSwitch, SUN_ICON, MOON_ICON
from ui.switch import Switch
from ui.notification_settings import notification_settings

BELL_ICON = "\U0001F514"
HOURS_PER_DAY = 24


class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("Settings")
        apply_live_style(title, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        # -- Appearance --------------------------------------------------
        appearance_label = QLabel("Appearance")
        apply_live_style(appearance_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 11px; font-weight: 700;")
        layout.addWidget(appearance_label)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(10)

        self._icon_label = QLabel()
        self._icon_label.setStyleSheet("font-size: 15px;")
        theme_row.addWidget(self._icon_label)

        self._mode_label = QLabel()
        apply_live_style(self._mode_label, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 13px;")
        theme_row.addWidget(self._mode_label)

        theme_row.addStretch()
        theme_row.addWidget(ToggleSwitch())

        layout.addLayout(theme_row)

        divider = QFrame()
        divider.setFixedHeight(1)
        apply_live_style(divider, lambda c: f"background-color: {c['BORDER']};")
        layout.addWidget(divider)

        # -- Stale pending/rejected threshold -----------------------------
        # No section header here on purpose -- this threshold isn't
        # notification-specific anymore, it also defines what counts as
        # "late" on the Late tab (see ui/Pages/Late.py).
        notify_row = QHBoxLayout()
        notify_row.setSpacing(10)

        icon_label = QLabel(BELL_ICON)
        icon_label.setStyleSheet("font-size: 15px;")
        notify_row.addWidget(icon_label)

        notify_text = QLabel("Notify me about pending/rejected requests that are taking a while")
        notify_text.setWordWrap(True)
        apply_live_style(notify_text, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 13px;")
        notify_row.addWidget(notify_text, stretch=1)

        self._notify_switch = Switch(checked=notification_settings.enabled)
        self._notify_switch.toggled.connect(self._on_notify_toggled)
        notify_row.addWidget(self._notify_switch)

        layout.addLayout(notify_row)

        threshold_row = QHBoxLayout()
        threshold_row.setSpacing(10)

        self._threshold_label = QLabel("Notify when waiting for at least")
        apply_live_style(self._threshold_label, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 12px;")
        threshold_row.addWidget(self._threshold_label)

        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(1, 999)
        self._threshold_spin.valueChanged.connect(self._on_threshold_changed)
        threshold_row.addWidget(self._threshold_spin)

        self._threshold_unit = QComboBox()
        self._threshold_unit.addItems(["Hours", "Days"])
        self._threshold_unit.currentIndexChanged.connect(self._on_unit_changed)
        threshold_row.addWidget(self._threshold_unit)

        threshold_row.addStretch()
        layout.addLayout(threshold_row)

        layout.addStretch()

        self._load_threshold_into_controls(notification_settings.threshold_hours)
        self._update_threshold_enabled(notification_settings.enabled)

        self._update_labels(theme_manager.mode)
        theme_manager.theme_changed.connect(self._update_labels)

    def _update_labels(self, mode):
        if mode == "dark":
            self._icon_label.setText(MOON_ICON)
            self._mode_label.setText("Dark mode")
        else:
            self._icon_label.setText(SUN_ICON)
            self._mode_label.setText("Light mode")

    def _load_threshold_into_controls(self, hours):
        if hours % HOURS_PER_DAY == 0 and hours >= HOURS_PER_DAY:
            self._threshold_spin.blockSignals(True)
            self._threshold_unit.blockSignals(True)
            self._threshold_spin.setValue(int(hours // HOURS_PER_DAY))
            self._threshold_unit.setCurrentText("Days")
            self._threshold_spin.blockSignals(False)
            self._threshold_unit.blockSignals(False)
        else:
            self._threshold_spin.blockSignals(True)
            self._threshold_unit.blockSignals(True)
            self._threshold_spin.setValue(int(hours))
            self._threshold_unit.setCurrentText("Hours")
            self._threshold_spin.blockSignals(False)
            self._threshold_unit.blockSignals(False)

    def _update_threshold_enabled(self, enabled):
        self._threshold_label.setEnabled(enabled)
        self._threshold_spin.setEnabled(enabled)
        self._threshold_unit.setEnabled(enabled)

    def _on_notify_toggled(self, checked):
        notification_settings.set_enabled(checked)
        self._update_threshold_enabled(checked)

    def _on_threshold_changed(self, _value):
        multiplier = HOURS_PER_DAY if self._threshold_unit.currentText() == "Days" else 1
        notification_settings.set_threshold_hours(self._threshold_spin.value() * multiplier)

    def _on_unit_changed(self, _index):
        # Re-express the ALREADY-STORED threshold in the newly selected
        # unit, rather than reinterpreting the same displayed number under
        # the new unit -- without this, switching "48 Hours" to "Days"
        # silently became "48 Days" (1152 hours), a 24x jump nobody asked
        # for. The number changes so the underlying hours value doesn't.
        hours = notification_settings.threshold_hours
        display_value = hours / HOURS_PER_DAY if self._threshold_unit.currentText() == "Days" else hours
        self._threshold_spin.blockSignals(True)
        self._threshold_spin.setValue(max(1, round(display_value)))
        self._threshold_spin.blockSignals(False)
        self._on_threshold_changed(self._threshold_spin.value())
