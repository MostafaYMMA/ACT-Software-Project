"""
Animated light/dark toggle: a sliding pill-shaped switch with a sun icon
in light mode and a moon icon in dark mode. Clicking it calls
theme_manager.toggle() directly - this isn't a fake visual switch, it
actually changes (and persists) the app's theme.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property, QRectF
from PySide6.QtGui import QPainter, QColor, QFont

from ui.theme_manager import theme_manager

TRACK_W = 56
TRACK_H = 28
HANDLE_MARGIN = 3
HANDLE_SIZE = TRACK_H - HANDLE_MARGIN * 2
SUN_ICON = "\u2600"     # ☀
MOON_ICON = "\u263E"    # ☾


class ToggleSwitch(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(TRACK_W, TRACK_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._is_dark = theme_manager.mode == "dark"
        self._handle_x = (
            float(TRACK_W - HANDLE_SIZE - HANDLE_MARGIN) if self._is_dark
            else float(HANDLE_MARGIN)
        )

        theme_manager.theme_changed.connect(self._on_theme_changed)

    # -- animatable property (Qt needs this exact getter/setter/Property pattern) --
    def _get_handle_x(self):
        return self._handle_x

    def _set_handle_x(self, value):
        self._handle_x = value
        self.update()

    handle_x = Property(float, _get_handle_x, _set_handle_x)

    def _on_theme_changed(self, mode):
        self._is_dark = (mode == "dark")
        target = (
            float(TRACK_W - HANDLE_SIZE - HANDLE_MARGIN) if self._is_dark
            else float(HANDLE_MARGIN)
        )
        anim = QPropertyAnimation(self, b"handle_x", self)
        anim.setDuration(200)
        anim.setStartValue(self._handle_x)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anim = anim  # prevent garbage collection mid-animation

    def mousePressEvent(self, event):
        theme_manager.toggle()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track_color = QColor("#3A3A38") if self._is_dark else QColor("#FFE3D2")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(0, 0, TRACK_W, TRACK_H, TRACK_H / 2, TRACK_H / 2)

        handle_rect = QRectF(self._handle_x, HANDLE_MARGIN, HANDLE_SIZE, HANDLE_SIZE)
        painter.setBrush(QColor("#fc6a28"))
        painter.drawEllipse(handle_rect)

        font = QFont()
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(QColor("white"))
        icon = MOON_ICON if self._is_dark else SUN_ICON
        painter.drawText(handle_rect, Qt.AlignmentFlag.AlignCenter, icon)