"""
Reusable "is this actually loading?" UI: a small rotating arc spinner
(drawn manually, no image files needed) plus a message label.

Two ways to use it:
  - LoadingOverlay: semi-transparent layer that covers a page while it
    loads, then gets hidden. Use this inside any page that does real
    background work (e.g. a scan, a sync) and wants to show a spinner
    over itself while that work runs.
  - Spinner: just the spinning icon by itself, e.g. embedded directly
    in the splash screen without needing a full overlay.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPen, QColor

from ui.theme import COLOR_ACCENT, COLOR_TEXT_PRIMARY
from PySide6.QtWidgets import QLabel


class Spinner(QWidget):
    """A rotating arc. Call start()/stop() to animate it."""

    def __init__(self, parent=None, size=32, color=COLOR_ACCENT, thickness=3):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._color = QColor(color)
        self._thickness = thickness
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._timer.start(16)  # ~60fps

    def stop(self):
        self._timer.stop()

    def _tick(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._color, self._thickness)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        margin = self._thickness + 1
        rect = self.rect().adjusted(margin, margin, -margin, -margin)
        painter.drawArc(rect, self._angle * 16, 100 * 16)


class LoadingOverlay(QWidget):
    """Covers its parent widget with a translucent layer + spinner +
    message while real work happens. Call start(message) to show it,
    stop() to hide it. The parent is responsible for calling
    reposition() on resize so the overlay keeps covering it fully."""

    def __init__(self, parent, message="Loading...",
                 spinner_color=COLOR_ACCENT, bg="rgba(255, 255, 255, 215)"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {bg};")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)

        self.spinner = Spinner(self, size=34, color=spinner_color)
        layout.addWidget(self.spinner, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.message_label = QLabel(message)
        self.message_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px;")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.message_label)

        self.hide()

    def reposition(self):
        if self.parent():
            self.setGeometry(0, 0, self.parent().width(), self.parent().height())

    def set_message(self, text):
        self.message_label.setText(text)

    def start(self, message=None):
        if message:
            self.set_message(message)
        self.reposition()
        self.spinner.start()
        self.show()
        self.raise_()

    def stop(self):
        self.spinner.stop()
        self.hide()