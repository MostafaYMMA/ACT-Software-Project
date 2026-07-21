"""
Singleton holding the local path to the OneDrive/SharePoint-synced
folder used by the SharePoint Update/View Current/Finalize buttons (see
ui/Pages/History.py, services/sync_service.py, services/sharepoint_service.py).
Persisted via QSettings, same pattern as ui/notification_settings.py and
ui/theme_manager.py, so it survives restarts without being re-typed.

Also holds the last-pasted OneDrive/SharePoint web link (see
services/onedrive_link_resolver.py) -- purely a convenience so the link
field on Settings is pre-filled next time, not something anything else
reads. `folder` (the resolved local path) is the only value the sync
channel itself actually uses.
"""

from PySide6.QtCore import QObject, Signal, QSettings


class SharePointSettings(QObject):
    folder_changed = Signal(str)
    link_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self._settings = QSettings("ACTSoftware", "TimecardApp")
        self._folder = self._settings.value("sharepoint_folder", "", type=str)
        self._link = self._settings.value("sharepoint_onedrive_link", "", type=str)

    @property
    def folder(self):
        return self._folder

    def set_folder(self, folder):
        folder = (folder or "").strip()
        if folder == self._folder:
            return
        self._folder = folder
        self._settings.setValue("sharepoint_folder", folder)
        self.folder_changed.emit(folder)

    @property
    def link(self):
        return self._link

    def set_link(self, link):
        link = (link or "").strip()
        if link == self._link:
            return
        self._link = link
        self._settings.setValue("sharepoint_onedrive_link", link)
        self.link_changed.emit(link)


# Import this instance everywhere - don't instantiate SharePointSettings yourself.
sharepoint_settings = SharePointSettings()
