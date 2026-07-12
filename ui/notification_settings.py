"""
Singleton controlling the pending/rejected staleness notification:
whether it's on, and how many hours old a pending/rejected entry has to
be before it counts. Persisted via QSettings, same pattern as
ui/theme_manager.py, so both survive restarts.
"""

from PySide6.QtCore import QObject, Signal, QSettings

DEFAULT_THRESHOLD_HOURS = 24.0


class NotificationSettings(QObject):
    settings_changed = Signal()

    def __init__(self):
        super().__init__()
        self._settings = QSettings("ACTSoftware", "TimecardApp")
        self._enabled = self._settings.value("notifications_enabled", False, type=bool)
        self._threshold_hours = float(
            self._settings.value("notifications_threshold_hours", DEFAULT_THRESHOLD_HOURS)
        )

    @property
    def enabled(self):
        return self._enabled

    @property
    def threshold_hours(self):
        return self._threshold_hours

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self._settings.setValue("notifications_enabled", enabled)
        self.settings_changed.emit()

    def set_threshold_hours(self, hours):
        hours = float(hours)
        if hours == self._threshold_hours:
            return
        self._threshold_hours = hours
        self._settings.setValue("notifications_threshold_hours", hours)
        self.settings_changed.emit()


# Import this instance everywhere - don't instantiate NotificationSettings yourself.
notification_settings = NotificationSettings()
