"""
ui/counting_label.py

A QLabel that displays an integer and can either:
  - start_spin(): show an indeterminate "still working" pulse (a gentle
    opacity blink) while the real count isn't known yet. This does NOT
    climb through fake numbers -- any guessed number could end up
    higher than the real target once the scan actually finishes, which
    meant the counter had to visibly count back DOWN to correct itself.
    A blink has no value to "overshoot", so that's no longer possible.
  - animate_to(target): the only place the displayed number actually
    changes -- always climbs from 0 straight up to the real target, at
    a speed that scales with the size of the target (so "3" and "300"
    both finish in a reasonable, proportionate amount of time), landing
    exactly on target. Never reverses, never overshoots.

Used by ui/Pages/Dashboard.py's StatCard for the Approved/Pending/
Rejected mail counters.
"""

from PySide6.QtWidgets import QLabel, QGraphicsOpacityEffect
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, Property

# Landing duration scales with the target so small and large counts both
# feel proportionate to how far they have to climb - clamped at both ends
# so neither extreme (0 or a huge number) looks wrong. Floor raised so
# small counts still get a visible climb instead of basically snapping;
# ceiling capped at 5s so nothing ever takes longer than that.
MIN_LANDING_MS = 1500
MAX_LANDING_MS = 5000
MS_PER_UNIT = 50  # extra ms per unit of target, before clamping

BLINK_DURATION_MS = 700
BLINK_LOW_OPACITY = 0.35


class CountingLabel(QLabel):
    def __init__(self, initial_text: str = "0", parent=None):
        super().__init__(initial_text, parent)
        self._value = 0

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._blink_anim = None
        self._landing_anim = None

    # -- animatable property, drives the landing tween ------------------------
    def _get_value(self) -> int:
        return self._value

    def _set_value(self, value: int):
        self._value = int(value)
        self.setText(str(self._value))

    displayValue = Property(int, _get_value, _set_value)

    # -- indeterminate "scanning" loading state: a gentle blink, not a guess ---
    def start_spin(self):
        self._stop_landing_anim()
        self._set_value(0)

        self._blink_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._blink_anim.setDuration(BLINK_DURATION_MS)
        self._blink_anim.setStartValue(1.0)
        self._blink_anim.setKeyValueAt(0.5, BLINK_LOW_OPACITY)
        self._blink_anim.setEndValue(1.0)
        self._blink_anim.setLoopCount(-1)  # repeats until stop_spin()
        self._blink_anim.start()

    def stop_spin(self):
        if self._blink_anim is not None:
            self._blink_anim.stop()
            self._blink_anim = None
        self._opacity_effect.setOpacity(1.0)

    # -- deterministic landing on a real value -----------------------------------
    def animate_to(self, target: int):
        self.stop_spin()
        self._stop_landing_anim()

        target = int(target)

        if target <= 0:
            # Still play a visible animated drop to 0 (unless already there),
            # so switching to a project type with zero records also reads as
            # "the counter re-ran" rather than a silent, unanimated snap.
            start = self._value
            if start == 0:
                return
            anim = QPropertyAnimation(self, b"displayValue", self)
            anim.setDuration(MIN_LANDING_MS)
            anim.setStartValue(start)
            anim.setEndValue(0)
            anim.setEasingCurve(QEasingCurve.Type.Linear)
            anim.finished.connect(self._clear_landing_anim)
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            self._landing_anim = anim
            return

        self._set_value(0)  # always climbs UP from zero, never down from a guess
        duration = min(MAX_LANDING_MS, max(MIN_LANDING_MS, target * MS_PER_UNIT))

        anim = QPropertyAnimation(self, b"displayValue", self)
        anim.setDuration(int(duration))
        anim.setStartValue(0)
        anim.setEndValue(target)
        # Linear, not an Out-curve: an Out curve moves fast at the start and
        # crawls for the last stretch, and with an integer property that
        # crawl shows up as several repeated frames of the same number
        # right before it lands - reads as stuttering/lag. Linear keeps the
        # per-frame increment roughly constant the whole way, so it climbs
        # evenly start to finish instead.
        anim.setEasingCurve(QEasingCurve.Type.Linear)
        # DeleteWhenStopped destroys the C++ animation as soon as it lands, so
        # the reference kept below outlives the object it points at. Dropping
        # it on "finished" is what keeps _stop_landing_anim from later calling
        # stop() on an animation Qt has already deleted -- that raises
        # "libshiboken: Internal C++ object already deleted". The signal fires
        # before the deferred deletion actually runs, so this always gets there
        # first.
        anim.finished.connect(self._clear_landing_anim)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._landing_anim = anim  # prevent garbage collection mid-animation

    def _clear_landing_anim(self):
        self._landing_anim = None

    def _stop_landing_anim(self):
        if self._landing_anim is None:
            return
        try:
            self._landing_anim.stop()
        except RuntimeError:
            # Already deleted underneath us by some path other than the
            # finished signal above -- there's nothing left to stop, and the
            # reference is cleared below either way.
            pass
        self._landing_anim = None

    def set_static_text(self, text: str):
        """For non-numeric states (e.g. the initial 'no data yet' placeholder
        '--') that shouldn't participate in spin/animate at all."""
        self.stop_spin()
        self._stop_landing_anim()
        self.setText(text)