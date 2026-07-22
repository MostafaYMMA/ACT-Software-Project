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

from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QRect, QTimer
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


# A table's rows arriving all at once reads as a flicker; revealed in
# order it reads as the grid filling up. Total time is FIXED rather than
# per-row: 12 rows and 4000 rows both finish in REVEAL_TOTAL_MS, the
# batch size absorbing the difference. A stagger of "N milliseconds per
# row" would look right on a small scan and take most of a minute after a
# big one, which is a hang, not an animation.
ROW_REVEAL_TOTAL_MS = 300
ROW_REVEAL_STEPS = 12


def reveal_rows(table, total_ms=ROW_REVEAL_TOTAL_MS, steps=ROW_REVEAL_STEPS):
    """
    Reveals a QTableWidget's rows top-to-bottom instead of showing them
    all at once. Safe to call on every repopulate: any reveal still in
    flight on this table is cancelled first, so quickly switching status
    tabs (or re-running a scan) can't leave two timers fighting over which
    rows are hidden -- which would strand rows hidden forever.

    Purely cosmetic. Rows are only ever hidden briefly and always end up
    shown, including when the table is repopulated mid-reveal.
    """
    row_count = table.rowCount()

    previous = getattr(table, "_row_reveal_timer", None)
    if previous is not None:
        previous.stop()
        table._row_reveal_timer = None

    if row_count == 0:
        return

    for row in range(row_count):
        table.setRowHidden(row, True)

    batch = max(1, -(-row_count // steps))  # ceil, so the last batch is never empty
    state = {"next_row": 0}
    timer = QTimer(table)
    timer.setInterval(max(1, total_ms // steps))

    def _step():
        start = state["next_row"]
        for row in range(start, min(start + batch, row_count)):
            table.setRowHidden(row, False)
        state["next_row"] = start + batch
        if state["next_row"] >= row_count:
            timer.stop()
            if getattr(table, "_row_reveal_timer", None) is timer:
                table._row_reveal_timer = None

    timer.timeout.connect(_step)
    table._row_reveal_timer = timer  # prevent garbage collection mid-reveal
    _step()  # show the first batch immediately, so nothing flashes empty
    timer.start()


def stagger_fade_in(widgets, duration=260, gap_ms=70):
    """
    Fades a row of widgets in one after another, so they arrive as a
    sequence rather than a block.

    Deliberately does NOT go through fade_in: that helper calls
    force_repaint on cleanup, which resizes the widget to its parent and
    hide/shows it. That's right for a full page filling a QStackedWidget
    and wrong for anything sitting in a layout -- it would rip a stat card
    out of its row. Here the effect is simply detached, which is the part
    that actually matters (an opacity effect left attached is what stops
    widgets like CountingLabel repainting properly afterwards).

    Safe to call repeatedly on the same widgets (it runs on every page
    show): anything still in flight is cancelled first. Without that,
    attaching the new effect DELETES the one the previous call's delayed
    animations still point at, and each of those then fires against a
    dead target -- Qt's "Changing state of an animation without target".
    """
    for index, widget in enumerate(widgets):
        _cancel_stagger(widget)

        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)

        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _cleanup(target=widget):
            target.setGraphicsEffect(None)
            target._stagger_anim = None

        anim.finished.connect(_cleanup)
        # Held on the widget so neither the animation nor the timer is
        # collected mid-flight, which would freeze the widget at whatever
        # opacity it had reached -- including 0, i.e. invisible.
        widget._stagger_anim = anim
        widget._stagger_timer = None
        if index == 0:
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        else:
            timer = QTimer(widget)
            timer.setSingleShot(True)
            timer.timeout.connect(
                lambda a=anim: a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            )
            widget._stagger_timer = timer
            timer.start(index * gap_ms)


def _cancel_stagger(widget):
    """Stops any stagger_fade_in still running on this widget and leaves it
    fully visible. Order matters: the pending timer is killed BEFORE the
    effect is torn down, so it can't fire at a target that no longer
    exists -- and the effect is detached rather than left at whatever
    opacity it had reached, which for a cancelled fade is usually 0."""
    timer = getattr(widget, "_stagger_timer", None)
    if timer is not None:
        timer.stop()
        widget._stagger_timer = None

    anim = getattr(widget, "_stagger_anim", None)
    if anim is not None:
        anim.stop()
        widget._stagger_anim = None

    if widget.graphicsEffect() is not None:
        widget.setGraphicsEffect(None)


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