"""
Singleton controlling the app's light/dark mode.

- Persists the choice via QSettings, so it's remembered across restarts
  (this is the actual "backend" - it's not just a visual switch).
- The instant the mode changes, it re-applies the app-level stylesheet,
  so anything styled via objectName selectors (see ui/theme.py) recolors
  immediately, everywhere, with no manual per-widget work needed.
- Anything that ALSO needs to react manually (like the toggle switch's
  own hand-drawn sun/moon) should connect to theme_changed.
"""

from PySide6.QtCore import QObject, Signal, QSettings
from PySide6.QtWidgets import QApplication

from ui import theme


class ThemeManager(QObject):
    theme_changed = Signal(str)  # emits "light" or "dark"

    def __init__(self):
        super().__init__()
        self._settings = QSettings("ACTSoftware", "TimecardApp")
        stored = self._settings.value("theme_mode", "light")
        self._mode = stored if stored in ("light", "dark") else "light"

    @property
    def mode(self):
        return self._mode

    def colors(self):
        return theme.DARK if self._mode == "dark" else theme.LIGHT

    def stylesheet(self):
        return theme.build_stylesheet(self.colors())

    def set_mode(self, mode):
        if mode not in ("light", "dark") or mode == self._mode:
            return
        self._mode = mode
        self._settings.setValue("theme_mode", mode)

        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(self.stylesheet())

        self.theme_changed.emit(mode)

    def toggle(self):
        self.set_mode("dark" if self._mode == "light" else "light")


# Import this instance everywhere - don't instantiate ThemeManager yourself.
theme_manager = ThemeManager()