"""
Shared-element "expand from the avatar" photo viewer, opened by
ui/avatar.py's Avatar._show_view_dialog (the "View photo" menu item).

Lives as two plain QWidget children of the app's ui.app.MainWindow (NOT
a QDialog, and NOT parented to widget.window() -- see
ui/avatar.py::_find_main_window for why that distinction matters). Being
real children of MainWindow, in the SAME coordinate space as the Avatar
widget itself, is what lets the enlarged photo be positioned with
mapTo()/setGeometry() at the avatar's exact on-screen spot, and lets the
scrim cover the whole window (top bar + sidebar included) rather than
just whatever area a QDialog would happen to sit over.

Reuses ProfileCircle's circular-clip pixmap painting approach (see
ui/profile_circle.py) rather than reimplementing image cropping --
_CircularPhoto below is the same clip-a-QPainterPath-ellipse technique,
generalized to a size that changes every frame instead of a fixed one.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import (
    QObject, Qt, QRect, QRectF, QPropertyAnimation, QParallelAnimationGroup, QEasingCurve, Property, QEvent,
)
from PySide6.QtGui import QPainter, QPainterPath, QColor

# The enlarged photo's diameter, and how long the grow/shrink takes.
EXPANDED_DIAMETER = 260
ANIMATION_MS = 320
# Scrim's fully-dimmed color -- opacity itself is animated (see _Scrim),
# so this alpha is the MAXIMUM darkness reached once fully open.
SCRIM_COLOR = QColor(0, 0, 0, 150)


class _CircularPhoto(QWidget):
    """A single circularly-clipped pixmap, painted at whatever size this
    widget's current geometry happens to be.

    Position and size are driven together by a single animated "progress"
    float (0.0-1.0, see PhotoLightbox._play), rather than animating Qt's
    built-in "geometry" QRect property directly -- geometry's own
    interpolation moves all four corners (left/top/right/bottom)
    independently, each rounded to an int on its own, which can leave
    the resulting width and height a stray pixel apart even when both
    endpoints are perfectly square. Computing one shared `size` value
    per frame here and using it for BOTH dimensions (see
    _apply_progress) makes that impossible instead of just unlikely --
    still one continuous "grow and travel" motion, just computed
    directly rather than left to QRect's component-wise interpolator."""

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self._source_pixmap = pixmap
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._start_rect = QRect()
        self._end_rect = QRect()
        self._progress = 0.0

    def set_motion_endpoints(self, start_rect, end_rect):
        """The rects this widget will travel/grow between on the next
        "progress" animation. Doesn't move the widget itself -- the
        animation's own start (progress=0.0) does that."""
        self._start_rect = start_rect
        self._end_rect = end_rect

    def _get_progress(self):
        return self._progress

    def _set_progress(self, value):
        self._progress = value
        self._apply_progress()

    progress = Property(float, _get_progress, _set_progress)

    def _apply_progress(self):
        t = self._progress
        start, end = self._start_rect, self._end_rect
        size = start.width() + (end.width() - start.width()) * t

        # Center computed with plain float math (x + width/2.0), NOT
        # QRect.center() -- QRect treats bottom-right as inclusive, so
        # its center() is (left+right)/2 with integer division, which for
        # an even width/height is off by half a pixel from x + width/2.
        # Reconstructing top-left from THAT center (as an earlier version
        # of this did) landed a pixel away from the real start/end rects
        # at t=0/t=1. Float x+width/2.0 round-trips exactly instead.
        start_cx, start_cy = start.x() + start.width() / 2.0, start.y() + start.height() / 2.0
        end_cx, end_cy = end.x() + end.width() / 2.0, end.y() + end.height() / 2.0
        cx = start_cx + (end_cx - start_cx) * t
        cy = start_cy + (end_cy - start_cy) * t

        size = round(size)
        self.setGeometry(QRect(round(cx - size / 2), round(cy - size / 2), size, size))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        size = self.width()  # == self.height() -- see class docstring
        rect = QRectF(0, 0, size, size)
        path = QPainterPath()
        path.addEllipse(rect)
        painter.setClipPath(path)

        if self._source_pixmap.isNull() or size <= 0:
            return
        scaled = self._source_pixmap.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (scaled.width() - size) / 2
        y = (scaled.height() - size) / 2
        painter.drawPixmap(-int(x), -int(y), scaled)


class _Scrim(QWidget):
    """Full-window dimming backdrop. Clicking anywhere on it dismisses
    the overlay -- it only ever sits BELOW the enlarged photo (see
    PhotoLightbox.__init__'s raise_() order), so a click that lands on
    the photo itself never reaches here, which is exactly "click outside
    the photo to dismiss" with no extra hit-testing needed."""

    def __init__(self, parent, on_dismiss):
        super().__init__(parent)
        self._on_dismiss = on_dismiss
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._dim_opacity = 0.0

    def _get_dim_opacity(self):
        return self._dim_opacity

    def _set_dim_opacity(self, value):
        self._dim_opacity = value
        self.update()

    dim_opacity = Property(float, _get_dim_opacity, _set_dim_opacity)

    def paintEvent(self, event):
        painter = QPainter(self)
        color = QColor(SCRIM_COLOR)
        color.setAlphaF(SCRIM_COLOR.alphaF() * self._dim_opacity)
        painter.fillRect(self.rect(), color)

    def mousePressEvent(self, event):
        self._on_dismiss()


class PhotoLightbox(QObject):
    """Owns one open/close cycle of the scrim + animated circular photo.

    A QObject (parented to main_window, like the scrim/photo widgets)
    purely so it can install itself as main_window's event filter below
    -- installEventFilter requires a real QObject, not a plain Python
    object. Also kept alive explicitly by the Avatar that created it
    (see Avatar._show_view_dialog's self._lightbox) for as long as the
    overlay is open.
    """

    def __init__(self, main_window, avatar, pixmap):
        super().__init__(main_window)
        self._main_window = main_window
        self._avatar = avatar
        self._closing = False
        self._group = None

        self._scrim = _Scrim(main_window, self._on_scrim_clicked)
        self._scrim.setGeometry(main_window.rect())

        self._photo = _CircularPhoto(pixmap, main_window)
        self._photo.setGeometry(self._avatar_geometry())

        # Explicit raise order: scrim above everything already in
        # main_window (top bar, sidebar, content -- whatever was there
        # before), then the photo above the scrim, so the photo is
        # always the topmost, clickable-through-to-nothing-below layer.
        self._scrim.raise_()
        self._photo.raise_()
        self._scrim.show()
        self._photo.show()

        # Keeps the scrim covering the whole window if it's resized
        # while the lightbox is open, rather than leaving stale gaps at
        # the old size.
        self._main_window.installEventFilter(self)

        self._play(opening=True)

    # -- geometry helpers --------------------------------------------
    def _avatar_geometry(self):
        """The avatar's CURRENT on-screen rect, mapped into
        main_window's coordinate space -- read fresh every time this is
        called (both when opening AND when closing), so a window resize/
        move between open and close targets the avatar's up-to-date spot,
        not wherever it was when the overlay first opened."""
        top_left = self._avatar.mapTo(self._main_window, self._avatar.rect().topLeft())
        return QRect(top_left, self._avatar.size())

    def _expanded_geometry(self):
        size = EXPANDED_DIAMETER
        center = self._main_window.rect().center()
        return QRect(center.x() - size // 2, center.y() - size // 2, size, size)

    # -- animation ------------------------------------------------------
    def _play(self, opening):
        # A group that already finished naturally (e.g. the open
        # animation ran to completion before the scrim was clicked) has
        # already been deleted by its own DeleteWhenStopped policy -- see
        # _on_group_finished, which clears this reference precisely so
        # a stale, already-deleted group is never .stop()'d here.
        if self._group is not None:
            self._group.stop()

        self._photo.set_motion_endpoints(
            self._photo.geometry(), self._expanded_geometry() if opening else self._avatar_geometry(),
        )
        progress_anim = QPropertyAnimation(self._photo, b"progress", self._photo)
        progress_anim.setStartValue(0.0)
        progress_anim.setEndValue(1.0)

        scrim_anim = QPropertyAnimation(self._scrim, b"dim_opacity", self._scrim)
        scrim_anim.setStartValue(self._scrim.dim_opacity)
        scrim_anim.setEndValue(1.0 if opening else 0.0)

        # OutCubic (fast start, settles smoothly into place) opening,
        # InCubic closing -- InCubic is OutCubic's exact time-reverse
        # (InCubic(t) == 1 - OutCubic(1-t)), so playing it with the
        # start/end swapped reads as literally undoing the open, not as
        # a second, unrelated animation.
        easing = QEasingCurve.Type.OutCubic if opening else QEasingCurve.Type.InCubic
        for anim in (progress_anim, scrim_anim):
            anim.setDuration(ANIMATION_MS)
            anim.setEasingCurve(easing)

        group = QParallelAnimationGroup(self._main_window)
        group.addAnimation(progress_anim)
        group.addAnimation(scrim_anim)
        group.finished.connect(self._on_group_finished)
        if not opening:
            group.finished.connect(self._cleanup)
        group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)
        self._group = group

    def _on_group_finished(self):
        # DeleteWhenStopped deletes the group as soon as it finishes --
        # whether that's a natural completion or an explicit .stop() call
        # from the NEXT _play() -- so this reference must be dropped
        # right away too, or a later _play() could try to .stop() an
        # already-deleted C++ object.
        self._group = None

    def _on_scrim_clicked(self):
        if self._closing:
            return
        self._closing = True
        self._play(opening=False)

    def eventFilter(self, obj, event):
        if obj is self._main_window and event.type() == QEvent.Type.Resize:
            self._scrim.setGeometry(self._main_window.rect())
        return False

    def _cleanup(self):
        self._main_window.removeEventFilter(self)
        self._scrim.deleteLater()
        self._photo.deleteLater()
