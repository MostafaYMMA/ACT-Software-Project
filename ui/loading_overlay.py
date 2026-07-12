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

Both recolor live with the app's light/dark theme via theme_manager.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPen, QColor

from ui.theme_manager import theme_manager
from ui.theme_utils import apply_live_style


def _rgba(hex_color, alpha):
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


class Spinner(QWidget):
    """A rotating arc. Call start()/stop() to animate it.

    color: fixed override (e.g. the splash screen always wants white,
    regardless of theme, since it sits on a solid-orange background).
    Leave it unset to track the current theme's accent color live."""

    def __init__(self, parent=None, size=32, thickness=3, color=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._thickness = thickness
        self._fixed_color = color
        self._color = QColor(color if color else theme_manager.colors()["ACCENT"])
        if color is None:
            theme_manager.theme_changed.connect(self._on_theme_changed)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def _on_theme_changed(self, _mode):
        self._color = QColor(theme_manager.colors()["ACCENT"])
        self.update()

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

    def __init__(self, parent, message="Loading..."):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        apply_live_style(self, lambda c: f"background-color: {_rgba(c['BG'], 215)};")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)

        self.spinner = Spinner(self, size=34)
        layout.addWidget(self.spinner, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.message_label = QLabel(message)
        apply_live_style(self.message_label, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 12px;")
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
