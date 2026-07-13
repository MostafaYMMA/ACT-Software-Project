"""
Animated boot splash: shown the instant the app launches, before the
account/select-account screens (see main.py). Replaces the old plain
"OSMO" text splash for this specific moment.

Look:
  - Background is theme-aware (white in light mode, near-black in dark
    mode - the same BG token every other page uses), NOT the fixed
    orange the old SplashPage still uses for the "Welcome back" screen.
  - The logo itself is always solid orange (assets/logo.png) regardless
    of theme - it never needs to change color, since orange reads fine
    on both a white and a near-black background.
  - Animation: the logo starts fully transparent, sitting just off the
    left edge of the screen, then fades in (opacity 0 -> 1) while
    sliding right, decelerating into its resting spot - which is
    right-of-center, not dead center (see _end_pos). Plays once,
    automatically, every time this widget is shown (showEvent), so no
    extra wiring is needed beyond swapping SplashPage for this class.
  - A spinner + status message sit fixed beneath the logo's resting
    spot (unaffected by the slide) - same start_loading()/stop_loading()
    API as the old SplashPage, so main.py doesn't need to change how it
    drives the loading state, only which class it instantiates.
"""

import os

from PySide6.QtWidgets import QWidget, QLabel, QGraphicsOpacityEffect
from PySide6.QtCore import Qt, QPoint, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QTimer
from PySide6.QtGui import QPixmap

from ui.theme_manager import theme_manager
from ui.theme_utils import apply_live_style
from ui.loading_overlay import Spinner

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_LOGO_PATH = os.path.join(_ASSETS_DIR, "logo.png")

LOGO_TARGET_WIDTH = 460  # bigger
ANIM_DURATION_MS = 1800  # medium: ~1.5-2s
REST_X_FRACTION = 0.5  # resting center-x as a fraction of window width (dead center)
REST_Y_FRACTION = 0.42  # resting center-y (leaves room for spinner/message below)


class BootLogoSplash(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        apply_live_style(self, lambda c: f"background-color: {c['BG']};")

        self._pixmap = self._load_logo_pixmap()

        self.logo_label = QLabel(self)
        self.logo_label.setPixmap(self._pixmap)
        self.logo_label.setFixedSize(self._pixmap.size())

        self._opacity_effect = QGraphicsOpacityEffect(self.logo_label)
        self._opacity_effect.setOpacity(0.0)
        self.logo_label.setGraphicsEffect(self._opacity_effect)

        self.spinner = Spinner(self, size=32)
        self.spinner.hide()

        self.message_label = QLabel("", self)
        apply_live_style(self.message_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 13px;")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.hide()

        self._anim_group = None
        self._slide_anim = None
        self._played_once = False

    @staticmethod
    def _load_logo_pixmap():
        pixmap = QPixmap(_LOGO_PATH)
        if pixmap.isNull():
            return pixmap
        scaled_height = int(pixmap.height() * (LOGO_TARGET_WIDTH / pixmap.width()))
        return pixmap.scaled(
            LOGO_TARGET_WIDTH, scaled_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    # -----------------------------------------------------------------
    # Layout (manual - the logo needs to be positioned/animated
    # independently of a normal box layout)
    # -----------------------------------------------------------------
    def _end_pos(self):
        w, h = self.width(), self.height()
        cx = int(w * REST_X_FRACTION)
        cy = int(h * REST_Y_FRACTION)
        return QPoint(cx - self.logo_label.width() // 2, cy - self.logo_label.height() // 2)

    def _start_pos(self):
        end = self._end_pos()
        return QPoint(-self.logo_label.width(), end.y())

    def _reposition_static_widgets(self):
        end = self._end_pos()
        below_y = end.y() + self.logo_label.height() + 24
        self.spinner.move(end.x() + self.logo_label.width() // 2 - self.spinner.width() // 2, below_y)
        self.message_label.setGeometry(0, below_y + self.spinner.height() + 10, self.width(), 20)

    def resizeEvent(self, event):
        if self._slide_anim is not None and self._slide_anim.state() == QPropertyAnimation.State.Running:
            # Window size changed mid-animation (e.g. the window maximizing
            # right after boot, a moment after the entrance already
            # started) - retarget the in-flight animation to the new
            # correct resting spot instead of leaving it aimed at the old,
            # smaller window's center.
            self._slide_anim.setEndValue(self._end_pos())
        else:
            self.logo_label.move(self._end_pos())
        self._reposition_static_widgets()
        super().resizeEvent(event)

    # -----------------------------------------------------------------
    # Animation
    # -----------------------------------------------------------------
    def showEvent(self, event):
        if not self._played_once:
            self._played_once = True
            # A zero-delay timer runs after any already-queued events (e.g.
            # the window's maximize resize, if one is pending) - this makes
            # it much more likely _play_entrance() computes its end
            # position from the window's real final size on the first
            # try, rather than relying solely on the resizeEvent retarget
            # safety net above.
            QTimer.singleShot(0, self._play_entrance)
        super().showEvent(event)

    def _play_entrance(self):
        self.logo_label.move(self._start_pos())
        self._opacity_effect.setOpacity(0.0)
        self._reposition_static_widgets()

        slide = QPropertyAnimation(self.logo_label, b"pos", self)
        slide.setDuration(ANIM_DURATION_MS)
        slide.setStartValue(self._start_pos())
        slide.setEndValue(self._end_pos())
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide_anim = slide

        fade = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        fade.setDuration(ANIM_DURATION_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(slide)
        group.addAnimation(fade)
        group.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anim_group = group  # prevent garbage collection mid-animation

    # -----------------------------------------------------------------
    # Loading state - same API as the old SplashPage, so main.py's
    # start_loading()/stop_loading() calls keep working unchanged.
    # -----------------------------------------------------------------
    def set_message(self, text):
        self.message_label.setText(text)
        self.message_label.setVisible(bool(text))

    def start_loading(self, message=None):
        if message:
            self.set_message(message)
        self.spinner.show()
        self.spinner.start()

    def stop_loading(self):
        self.spinner.stop()
        self.spinner.hide()
        self.set_message("")