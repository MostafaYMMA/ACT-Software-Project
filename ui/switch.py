"""
Generic animated on/off pill switch (no icons, no theme_manager coupling)
-- unlike ui/toggle_switch.py, which is hardwired specifically to the
light/dark toggle, this one just tracks its own checked state and emits
toggled(bool), so any settings row can drive it.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, Property, QRectF
from PySide6.QtGui import QPainter, QColor

from ui.theme_manager import theme_manager

TRACK_W = 44
TRACK_H = 24
HANDLE_MARGIN = 3
HANDLE_SIZE = TRACK_H - HANDLE_MARGIN * 2


class Switch(QWidget):
    toggled = Signal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setFixedSize(TRACK_W, TRACK_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._checked = checked
        self._handle_x = (
            float(TRACK_W - HANDLE_SIZE - HANDLE_MARGIN) if checked
            else float(HANDLE_MARGIN)
        )
        theme_manager.theme_changed.connect(lambda _mode: self.update())

    def _get_handle_x(self):
        return self._handle_x

    def _set_handle_x(self, value):
        self._handle_x = value
        self.update()

    handle_x = Property(float, _get_handle_x, _set_handle_x)

    def isChecked(self):
        return self._checked

    def setChecked(self, checked):
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        target = (
            float(TRACK_W - HANDLE_SIZE - HANDLE_MARGIN) if checked
            else float(HANDLE_MARGIN)
        )
        anim = QPropertyAnimation(self, b"handle_x", self)
        anim.setDuration(180)
        anim.setStartValue(self._handle_x)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anim = anim  # prevent garbage collection mid-animation

    def mousePressEvent(self, event):
        self.setChecked(not self._checked)
        self.toggled.emit(self._checked)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        colors = theme_manager.colors()
        track_color = QColor(colors["ACCENT"] if self._checked else colors["BORDER"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(0, 0, TRACK_W, TRACK_H, TRACK_H / 2, TRACK_H / 2)

        handle_rect = QRectF(self._handle_x, HANDLE_MARGIN, HANDLE_SIZE, HANDLE_SIZE)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(handle_rect)
