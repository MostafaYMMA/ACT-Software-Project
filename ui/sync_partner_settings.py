"""
Singleton holding the partner email used for cross-device sync via Update/Finalize.
Persisted via QSettings so it survives app restarts and is available in both
Settings and Export History pages.
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


sync_partner_settings = SyncPartnerSettings()
