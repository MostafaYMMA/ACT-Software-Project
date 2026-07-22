"""
Singleton holding the other user's email address -- where Update/Finalize
(see ui/Pages/History.py) send sync mail to. Persisted via QSettings, same
pattern as ui/notification_settings.py and ui/theme_manager.py, so it
survives restarts without needing to be re-typed every session.
"""

from PySide6.QtCore import QObject, Signal, QSettings


class SyncPartnerSettings(QObject):
    partner_email_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self._settings = QSettings("ACTSoftware", "TimecardApp")
        self._partner_email = self._settings.value("sync_partner_email", "", type=str)

    @property
    def partner_email(self):
        return self._partner_email

    def set_partner_email(self, email):
        email = (email or "").strip()
        if email == self._partner_email:
            return
        self._partner_email = email
        self._settings.setValue("sync_partner_email", email)
        self.partner_email_changed.emit(email)


# Import this instance everywhere - don't instantiate SyncPartnerSettings yourself.
sync_partner_settings = SyncPartnerSettings()
