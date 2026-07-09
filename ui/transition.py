"""
Small helpers for animated transitions between pages/phases:
  - fade_in: opacity fade, used for every normal page switch.
  - zoom_in: scale-up-from-center, layered on top of a fade specifically
    for "entering the account" (welcome splash -> main dashboard), so
    that moment feels more like arriving somewhere vs. a flat page swap.
  - force_repaint: works around a Qt/Windows quirk where a widget that
    was hidden inside a QStackedWidget doesn't actually redraw when it
    becomes current again - it stays blank until something forces a
    real repaint (previously: only fixed by manually resizing the window).
"""

from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QRect
from PySide6.QtWidgets import QGraphicsOpacityEffect, QStackedWidget, QWidget


def fade_in(widget, duration=250):
    """Fades a widget in from transparent to opaque. The opacity effect
    is removed once the animation finishes - leaving it attached
    afterward is what was causing some widgets (tables, spinners) to
    stop repainting properly on later page switches."""
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _cleanup():
        widget.setGraphicsEffect(None)
        force_repaint(widget)

    anim.finished.connect(_cleanup)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    widget._fade_anim = anim  # prevent garbage collection


def zoom_in(widget, duration=320, start_scale=0.85):
    """Scales a widget up from start_scale to its full current size,
    centered. Meant to be called alongside fade_in (not instead of it)
    for a specific "arriving" moment, e.g. entering the main app."""
    target_rect = widget.geometry()
    w, h = target_rect.width(), target_rect.height()
    if w <= 0 or h <= 0:
        return  # nothing sensible to animate yet
    shrink_w, shrink_h = int(w * start_scale), int(h * start_scale)
    center = target_rect.center()
    start_rect = QRect(
        center.x() - shrink_w // 2,
        center.y() - shrink_h // 2,
        shrink_w, shrink_h,
    )
    anim = QPropertyAnimation(widget, b"geometry", widget)
    anim.setDuration(duration)
    anim.setStartValue(start_rect)
    anim.setEndValue(target_rect)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    widget._zoom_anim = anim  # prevent garbage collection


def force_repaint(widget):
    """Forces Qt to actually redraw a widget and its children right now,
    instead of leaving it blank until the window is resized."""
    if widget.parent() is not None:
        widget.resize(widget.parent().size())
    widget.hide()
    widget.show()
    widget.raise_()
    widget.updateGeometry()
    widget.update()
    for child in widget.findChildren(QWidget):
        child.update()


class FadeStackedWidget(QStackedWidget):
    """A QStackedWidget where switching to a widget fades it in AND
    forces it to actually repaint immediately. Drop-in replacement for
    QStackedWidget - use setCurrentWidget() as normal.
    """

    def setCurrentWidget(self, widget):
        super().setCurrentWidget(widget)
        force_repaint(widget)
        fade_in(widget)