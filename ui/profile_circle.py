"""
Shared "photo or initials, cropped to a circle" painter.

Used by:
  - ui/avatar.py's Avatar (adds the click -> View/Choose/Remove menu)
  - ui/select_account_page.py's AccountTile (read-only - just shows
    whatever photo, if any, was already chosen for that account)

Both read/write the same QSettings key, so a photo picked from the top
bar's Avatar shows up on the select-account tile too, and vice versa.
"""

import os

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QSettings, QRectF
from PySide6.QtGui import QPainter, QPainterPath, QPixmap, QColor, QFont

from ui.theme_manager import theme_manager

SETTINGS_ORG = "ACTSoftware"
SETTINGS_APP = "TimecardApp"


def photo_path_for(username):
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    return settings.value(f"profile_photo/{username}", "")


class ProfileCircle(QWidget):
    def __init__(self, username, size=44, parent=None):
        super().__init__(parent)
        self.username = username
        self._size = size
        self.setFixedSize(size, size)
        self._pixmap = None
        self.reload()

    def reload(self):
        """Re-reads the saved photo path and repaints. AccountTile doesn't
        need to call this manually - the select-account grid is rebuilt
        fresh every time that page is shown, so a new ProfileCircle (and
        therefore a fresh reload()) happens automatically."""
        path = photo_path_for(self.username)
        if path and os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                self._pixmap = pix
                self.update()
                return
        self._pixmap = None
        self.update()

    def _initials(self):
        parts = self.username.split()
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0, 0, self._size, self._size)
        path = QPainterPath()
        path.addEllipse(rect)
        painter.setClipPath(path)

        if self._pixmap is not None:
            scaled = self._pixmap.scaled(
                self._size, self._size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (scaled.width() - self._size) / 2
            y = (scaled.height() - self._size) / 2
            painter.drawPixmap(-int(x), -int(y), scaled)
        else:
            colors = theme_manager.colors()
            painter.fillPath(path, QColor(colors["ACCENT"]))
            painter.setPen(QColor(colors["TEXT_ON_ACCENT"]))
            font = QFont()
            font.setPointSize(max(10, int(self._size * 0.32)))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._initials())