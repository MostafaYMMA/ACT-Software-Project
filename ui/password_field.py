"""
PasswordField - a QLineEdit for passwords with a hand-painted, animated
eye toggle embedded on the right side to show/hide the text.

Drop-in replacement for a bare QLineEdit(EchoMode.Password): same
text()/clear()/setFocus()/returnPressed surface, so callers don't need
to change anything except the construction line.

Styling follows ui/select_account_page.py's current pattern (see
ui/theme_utils.py): apply_live_style() re-applies the stylesheet
immediately AND every time theme_manager.theme_changed fires, so this
recolors live in both light and dark mode with no manual wiring at the
call site. The default look matches the QLineEdit style previously
inlined in select_account_page.py exactly, just with extra right
padding reserved for the eye icon.

Icon notes (see ui/toggle_switch.py, ui/switch.py for the same pattern
used elsewhere in this app):
  - hand-painted with QPainter, not an image asset
  - animated via QPropertyAnimation-driven float Properties
  - recolors live via theme_manager.colors() + theme_manager.theme_changed

Blink animation (tuned for smoothness):
  - Two-phase QSequentialAnimationGroup: close, then reopen. Both legs
    use non-overshooting easing curves (InOutSine / OutQuad) so the
    motion is continuous with no bounce/overshoot artifacts.
  - The pupil / hidden-slash glyph fades in and out continuously via
    setOpacity() as a function of the squash value, instead of a hard
    cutoff - eliminates the visible pop a binary cutoff would cause
    partway through the motion.
  - The visible/hidden state flips at the fully-shut instant, so the
    icon swap is hidden inside the blink rather than a jump-cut.
"""

from PySide6.QtWidgets import QWidget, QLineEdit, QVBoxLayout
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QSequentialAnimationGroup, Property, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen

from ui.theme_manager import theme_manager
from ui.theme_utils import apply_live_style

ICON_SIZE = 20
BLINK_CLOSE_MS = 110
BLINK_OPEN_MS = 170
RIGHT_MARGIN = 8
# Extra horizontal room the QLineEdit reserves so typed text never runs
# under the icon.
ICON_RESERVED_PX = ICON_SIZE + RIGHT_MARGIN + 10

# Squash range over which the pupil/slash glyph fades in and out.
FADE_START = 0.15
FADE_END = 0.65


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class EyeToggleButton(QWidget):
    """Hand-painted eye icon. Click plays a smooth 'blink' (ease-shut,
    then ease back open, no bounce/overshoot) and flips password
    visibility at the moment the eye is fully shut."""

    toggled = Signal(bool)  # emits True when password becomes visible

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(ICON_SIZE, ICON_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._visible_password = False
        self._squash = 1.0  # 1.0 = fully open, ~0 = fully shut (mid-blink)

        theme_manager.theme_changed.connect(lambda _mode: self.update())

    # -- animatable property --------------------------------------------------
    def _get_squash(self) -> float:
        return self._squash

    def _set_squash(self, value: float):
        self._squash = value
        self.update()

    eyeSquash = Property(float, _get_squash, _set_squash)

    # -- state -----------------------------------------------------------------
    def isPasswordVisible(self) -> bool:
        return self._visible_password

    # -- interaction -------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._play_blink()
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def _play_blink(self):
        close_anim = QPropertyAnimation(self, b"eyeSquash", self)
        close_anim.setDuration(BLINK_CLOSE_MS)
        close_anim.setStartValue(self._squash)
        close_anim.setEndValue(0.02)
        close_anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        open_anim = QPropertyAnimation(self, b"eyeSquash", self)
        open_anim.setDuration(BLINK_OPEN_MS)
        open_anim.setStartValue(0.02)
        open_anim.setEndValue(1.0)
        open_anim.setEasingCurve(QEasingCurve.Type.OutQuad)

        group = QSequentialAnimationGroup(self)
        group.addAnimation(close_anim)
        group.addAnimation(open_anim)
        close_anim.finished.connect(self._flip_state)
        group.start(QSequentialAnimationGroup.DeletionPolicy.DeleteWhenStopped)
        self._blink_anim = group  # prevent garbage collection mid-animation

    def _flip_state(self):
        self._visible_password = not self._visible_password
        self.toggled.emit(self._visible_password)

    # -- paint -----------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        colors = theme_manager.colors()
        color = QColor(colors["ACCENT"] if self.underMouse() else colors["TEXT_SECONDARY"])
        painter.setPen(QPen(color, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        cx, cy = self.width() / 2, self.height() / 2
        eye_w = 14.0
        eye_h = max(8.0 * self._squash, 0.8)
        rect = QRectF(cx - eye_w / 2, cy - eye_h / 2, eye_w, eye_h)

        painter.drawEllipse(rect)

        # Continuous fade instead of a hard cutoff, so the pupil/slash
        # never "pops" in or out partway through the blink.
        detail_opacity = _clamp((self._squash - FADE_START) / (FADE_END - FADE_START))
        if detail_opacity > 0.0:
            painter.setOpacity(detail_opacity)
            if self._visible_password:
                pupil_r = 2.2
                painter.setBrush(color)
                painter.drawEllipse(QRectF(cx - pupil_r, cy - pupil_r, pupil_r * 2, pupil_r * 2))
            else:
                painter.drawLine(
                    QPointF(cx - eye_w / 2 + 1, cy - eye_h / 2 + 1),
                    QPointF(cx + eye_w / 2 - 1, cy + eye_h / 2 - 1),
                )
            painter.setOpacity(1.0)


def _default_input_style(colors) -> str:
    """Matches the QLineEdit look previously inlined in
    select_account_page.py, with extra right padding for the icon."""
    return f"""
        QLineEdit {{
            background-color: {colors['BG']}; color: {colors['TEXT_PRIMARY']};
            border: 1px solid {colors['BORDER']}; border-radius: 6px;
            padding: 8px; padding-right: {ICON_RESERVED_PX}px;
        }}
        QLineEdit:focus {{ border: 1px solid {colors['ACCENT']}; }}
    """


class PasswordField(QWidget):
    """Drop-in replacement for a bare QLineEdit(EchoMode.Password)."""

    returnPressed = Signal()

    def __init__(self, placeholder: str = "Password", style_fn=None, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._input = QLineEdit(self)
        self._input.setEchoMode(QLineEdit.EchoMode.Password)
        self._input.setPlaceholderText(placeholder)
        self._input.returnPressed.connect(self.returnPressed)
        apply_live_style(self._input, style_fn or _default_input_style)
        layout.addWidget(self._input)

        self._eye = EyeToggleButton(self._input)
        self._eye.toggled.connect(self._on_eye_toggled)
        self._reposition_eye()

    def _on_eye_toggled(self, is_visible: bool):
        self._input.setEchoMode(
            QLineEdit.EchoMode.Normal if is_visible else QLineEdit.EchoMode.Password
        )

    def resizeEvent(self, event):
        self._reposition_eye()
        super().resizeEvent(event)

    def _reposition_eye(self):
        x = self._input.width() - self._eye.width() - RIGHT_MARGIN
        y = (self._input.height() - self._eye.height()) // 2
        self._eye.move(max(x, 0), max(y, 0))

    # -- QLineEdit-compatible surface, so callers don't need to change --------
    def text(self) -> str:
        return self._input.text()

    def clear(self):
        self._input.clear()

    def setFocus(self):
        self._input.setFocus()