"""
Singleton holding the currently-selected project type ("beverage" /
"hospitality" / None for "All"), shared between the Dashboard's live
filter and the Export History page's export scope - toggling it on
either page updates the other automatically.

Same pattern as ui/theme_manager.py and ui/notification_settings.py:
a QObject singleton with a Signal, persisted via QSettings so the
choice survives restarts too.
"""

from PySide6.QtCore import QObject, Signal, QSettings

_VALID_VALUES = (None, "beverage", "hospitality")


class ProjectTypeSettings(QObject):
    project_type_changed = Signal(object)  # object, since the value can be None

    def __init__(self):
        super().__init__()
        self._settings = QSettings("ACTSoftware", "TimecardApp")
        stored = self._settings.value("selected_project_type", None)
        self._project_type = stored if stored in _VALID_VALUES else None

    @property
    def project_type(self):
        return self._project_type

    def set_project_type(self, project_type):
        if project_type not in _VALID_VALUES:
            return
        if project_type == self._project_type:
            return  # no-op guard - this is what stops the two pages' button
            # groups from ever ping-ponging each other in an infinite loop
            # (see the "sync" handler in Dashboard.py/History.py)
        self._project_type = project_type
        self._settings.setValue("selected_project_type", project_type)
        self.project_type_changed.emit(project_type)


# Import this instance everywhere - don't instantiate ProjectTypeSettings yourself.
project_type_settings = ProjectTypeSettings()