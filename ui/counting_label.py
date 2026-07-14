"""
ui/counting_label.py

A QLabel that displays an integer and can either:
  - start_spin(): play an indeterminate "still working" loading
    animation -- a low, capped upward jitter, so a stat card doesn't
    sit frozen on a placeholder for however long a scan takes.
  - animate_to(target): tween smoothly from whatever's currently shown
    up to a real, known value -- an odometer-style count-up landing,
    not a jump-cut.

Used by ui/Pages/Dashboard.py's StatCard for the Approved/Pending/
Rejected mail counters.
"""

import random

from PySide6.QtWidgets import QLabel
from PySide6.QtCore import QTimer, QPropertyAnimation, QEasingCurve, Property

SPIN_INTERVAL_MS = 70
# Loading jitter never climbs past this -- keeps the illusion subtle and
# believable instead of spinning up to some huge, obviously-fake number.
SPIN_CEILING = 8
LANDING_DURATION_MS = 550


class CountingLabel(QLabel):
    def __init__(self, initial_text: str = "0", parent=None):
        super().__init__(initial_text, parent)
        self._value = 0

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(SPIN_INTERVAL_MS)
        self._spin_timer.timeout.connect(self._tick_spin)

        self._landing_anim = None

    # -- animatable property, drives the landing tween ------------------------
    def _get_value(self) -> int:
        return self._value

    def _set_value(self, value: int):
        self._value = int(value)
        self.setText(str(self._value))

    displayValue = Property(int, _get_value, _set_value)

    # -- indeterminate "scanning" loading state --------------------------------
    def start_spin(self):
        if self._landing_anim is not None:
            self._landing_anim.stop()
            self._landing_anim = None
        self._value = 0
        self.setText("0")
        self._spin_timer.start()

    def _tick_spin(self):
        if self._value < SPIN_CEILING:
            self._value += random.randint(0, 2)
            self._value = min(self._value, SPIN_CEILING)
            self.setText(str(self._value))

    def stop_spin(self):
        self._spin_timer.stop()

    # -- deterministic landing on a real value -----------------------------------
    def animate_to(self, target: int, duration_ms: int = LANDING_DURATION_MS):
        self.stop_spin()
        start = self._value
        if start == target:
            self._set_value(target)
            return

        anim = QPropertyAnimation(self, b"displayValue", self)
        anim.setDuration(duration_ms)
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._landing_anim = anim  # prevent garbage collection mid-animation

    def set_static_text(self, text: str):
        """For non-numeric states (e.g. the initial 'no data yet' placeholder
        '--') that shouldn't participate in spin/animate at all."""
        self.stop_spin()
        if self._landing_anim is not None:
            self._landing_anim.stop()
            self._landing_anim = None
        self.setText(text)