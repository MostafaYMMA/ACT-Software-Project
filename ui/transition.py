"""
Small helpers for animated transitions between pages/phases, so
account-creation -> select-account -> main-app (and page switches
inside the main app) don't just snap instantly.
"""

from PySide6.QtCore import QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import QGraphicsOpacityEffect, QStackedWidget


def fade_in(widget, duration=250):
    """Fades a widget in from transparent to opaque. Keeps a reference to
    the animation on the widget itself so it isn't garbage-collected
    mid-animation."""
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    widget._fade_anim = anim  # prevent garbage collection


class FadeStackedWidget(QStackedWidget):
    """A QStackedWidget where switching to a widget fades it in.
    Drop-in replacement for QStackedWidget - use setCurrentWidget() as normal.
    """

    def setCurrentWidget(self, widget):
        super().setCurrentWidget(widget)
        fade_in(widget)