"""
Singleton holding the optional preset email-body text used when the Late
tab's "Send Mail" button opens a mailto: compose window (see
ui/Pages/Late.py::_send_mail_for_row). Persisted via QSettings, same
pattern as ui/notification_settings.py, so it survives app restarts.

Static text only -- no per-record placeholders or templating, whatever is
stored is used exactly as typed. Empty/unset means "no body parameter at
all", i.e. the same compose window as before this setting existed.
"""

from PySide6.QtCore import QObject, Signal, QSettings


class LateMailBodySettings(QObject):
    settings_changed = Signal()

    def __init__(self):
        super().__init__()
        self._settings = QSettings("ACTSoftware", "TimecardApp")
        self._preset_body = self._settings.value("late_mail_preset_body", "", type=str)

    @property
    def preset_body(self):
        return self._preset_body

    def set_preset_body(self, text):
        text = text or ""
        if text == self._preset_body:
            return
        self._preset_body = text
        self._settings.setValue("late_mail_preset_body", text)
        self.settings_changed.emit()


# Import this instance everywhere - don't instantiate LateMailBodySettings yourself.
late_mail_body_settings = LateMailBodySettings()
