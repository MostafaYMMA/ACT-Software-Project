"""
Sidebar nav item: icon + label, with the icon doing a small animated
"pop" on hover. Split into two separate QLabels (icon, text) inside a
QFrame instead of one QPushButton with combined text, specifically so
the icon alone can be animated independently of the label.

Uses the same Property + QPropertyAnimation pattern as ui/toggle_switch.py,
for consistency with the rest of the codebase.
"""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, Property

ICON_BASE_PT = 13
ICON_HOVER_PT = 17
ICON_ANIM_MS = 180


class NavButton(QFrame):
    clicked = Signal()

    def __init__(self, icon, label, parent=None):
        super().__init__(parent)
        self.setObjectName("navButton")
        # Needed for QSS :hover to apply to a QFrame (QPushButton tracks
        # this automatically; a plain QFrame doesn't unless told to).
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._icon_pt = float(ICON_BASE_PT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 12, 24, 12)
        layout.setSpacing(10)

        self.icon_label = QLabel(icon)
        self.icon_label.setStyleSheet(f"background: transparent; font-size: {ICON_BASE_PT}pt;")
        layout.addWidget(self.icon_label)

        self.text_label = QLabel(label)
        self.text_label.setStyleSheet("background: transparent; font-size: 13px;")
        layout.addWidget(self.text_label)

        layout.addStretch()

    # -- animatable property (icon font size, in points) --
    def _get_icon_pt(self):
        return self._icon_pt

    def _set_icon_pt(self, value):
        self._icon_pt = value
        self.icon_label.setStyleSheet(f"background: transparent; font-size: {value:.1f}pt;")

    icon_pt = Property(float, _get_icon_pt, _set_icon_pt)

    def _animate_icon(self, target):
        anim = QPropertyAnimation(self, b"icon_pt", self)
        anim.setDuration(ICON_ANIM_MS)
        anim.setStartValue(self._icon_pt)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutBack)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._icon_anim = anim  # prevent garbage collection mid-animation

    def enterEvent(self, event):
        self._animate_icon(ICON_HOVER_PT)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_icon(ICON_BASE_PT)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)