"""
Sidebar nav item: icon + label, with the icon doing a small animated
"pop" on hover. Split into two separate QLabels (icon, text) inside a
QFrame instead of one QPushButton with combined text, specifically so
the icon alone can be animated independently of the label.

Icons are small inline SVGs rendered to solid-white pixmaps, not
Unicode glyphs. Unicode symbols (e.g. the old "⚠") get hijacked by
whatever color emoji font the OS ships - that's why the warning
triangle was rendering yellow instead of matching the rest of the
sidebar. Real vector icons stay pure white and crisp at any size.

Uses the same Property + QPropertyAnimation pattern as ui/toggle_switch.py,
for consistency with the rest of the codebase.
"""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, Property, QByteArray
from PySide6.QtGui import QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

ICON_BASE_PX = 20
ICON_HOVER_PX = 26
ICON_ANIM_MS = 180

# One simple, monochrome vector icon per nav key, each chosen to match
# its label rather than being a generic shape:
#   dashboard -> tiled panels (a dashboard is made of panels/widgets)
#   records   -> a document with rows (a "record" is a row of data)
#   current_sheet -> a spreadsheet grid with one cell filled in (a working
#                sheet you mark up, not just another list of records)
#   export    -> a clock with a return arrow (history, not just "refresh")
#   late      -> a clock with an exclamation mark (overdue, not a
#                generic hazard triangle - which is what read as an
#                unrelated "yellow warning" before)
#   settings  -> a toothed cog (it spins on hover, so it has to actually
#                look like something that turns)
_ICONS = {
    "dashboard": """
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <rect x="3" y="3" width="8" height="8" rx="1.6" fill="white"/>
          <rect x="13" y="3" width="8" height="5" rx="1.6" fill="white"/>
          <rect x="13" y="10" width="8" height="11" rx="1.6" fill="white"/>
          <rect x="3" y="13" width="8" height="8" rx="1.6" fill="white"/>
        </svg>
    """,
    "records": """
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <rect x="4" y="2.5" width="16" height="19" rx="2" fill="none" stroke="white" stroke-width="1.8"/>
          <line x1="7.5" y1="8" x2="16.5" y2="8" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
          <line x1="7.5" y1="12.5" x2="16.5" y2="12.5" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
          <line x1="7.5" y1="17" x2="13" y2="17" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
        </svg>
    """,
    "current_sheet": """
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <rect x="3" y="4" width="18" height="16" rx="2" fill="none" stroke="white" stroke-width="1.8"/>
          <line x1="3" y1="9" x2="21" y2="9" stroke="white" stroke-width="1.8"/>
          <line x1="9" y1="9" x2="9" y2="20" stroke="white" stroke-width="1.8"/>
          <rect x="10.2" y="10.2" width="9.6" height="3.4" rx="0.8" fill="white"/>
        </svg>
    """,
    "export": """
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <circle cx="11.5" cy="12.5" r="8" fill="none" stroke="white" stroke-width="1.8"/>
          <path d="M11.5 8v4.7l3.2 2" fill="none" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M6.2 4.6 3.3 4l.4 3.2" fill="none" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
    """,
    "late": """
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <circle cx="12" cy="13" r="8.2" fill="none" stroke="white" stroke-width="1.8"/>
          <line x1="12" y1="9" x2="12" y2="13.6" stroke="white" stroke-width="1.9" stroke-linecap="round"/>
          <circle cx="12" cy="17" r="1.15" fill="white"/>
          <line x1="9" y1="2.5" x2="15" y2="2.5" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
        </svg>
    """,
    # A real toothed cog: solid body with eight square teeth around it and
    # a cut-out hub, rather than the thin spoked circle this used to be
    # (which read as a sun/asterisk more than a gear). Drawn as one filled
    # path with evenodd so the hub is a genuine hole -- that matters because
    # the icon is rotated on hover (see app.py's _SETTINGS_SPIN_DEG), and a
    # hole stays a hole under rotation while a hub drawn in the background
    # colour would smear.
    "settings": """
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <path fill-rule="evenodd" clip-rule="evenodd" fill="white"
                d="M10.3 1.8h3.4l.35 2.6a8.2 8.2 0 0 1 1.87.78l2.1-1.6 2.4 2.4-1.6 2.1c.33.58.6 1.21.78 1.87l2.6.35v3.4l-2.6.35a8.2 8.2 0 0 1-.78 1.87l1.6 2.1-2.4 2.4-2.1-1.6a8.2 8.2 0 0 1-1.87.78l-.35 2.6h-3.4l-.35-2.6a8.2 8.2 0 0 1-1.87-.78l-2.1 1.6-2.4-2.4 1.6-2.1a8.2 8.2 0 0 1-.78-1.87l-2.6-.35v-3.4l2.6-.35c.18-.66.45-1.29.78-1.87l-1.6-2.1 2.4-2.4 2.1 1.6c.58-.33 1.21-.6 1.87-.78zM12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z"/>
        </svg>
    """,
}


def render_icon(key, size_px):
    """Render one of the icons above to a transparent, solid-white
    pixmap at the given pixel size. Shared with app.py's IconRail so
    the rail and the labeled panel always use the identical icon."""
    svg = _ICONS.get(key)
    if not svg:
        return QPixmap()
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size_px, size_px)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


class NavButton(QFrame):
    clicked = Signal()

    def __init__(self, icon_key, label, parent=None):
        super().__init__(parent)
        self.setObjectName("navButton")
        # Needed for QSS :hover to apply to a QFrame (QPushButton tracks
        # this automatically; a plain QFrame doesn't unless told to).
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.icon_key = icon_key
        self._icon_px = float(ICON_BASE_PX)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setFixedWidth(ICON_HOVER_PX + 4)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background: transparent;")
        self.icon_label.setPixmap(render_icon(icon_key, ICON_BASE_PX))
        # Without this, Qt sends enterEvent/leaveEvent to whichever CHILD
        # widget is directly under the cursor, not to this parent frame -
        # so moving onto this label would fire a spurious leaveEvent on
        # NavButton (shrinking the icon back down every time), and hovering
        # the label itself would never trigger anything at all. Making it
        # mouse-transparent means the parent is the only thing that ever
        # sees enter/leave, across the icon, the text, and the gap between
        # them - one continuous hover, no matter where inside the row.
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.icon_label)

        self.text_label = QLabel(label)
        self.text_label.setStyleSheet(
            "background: transparent; color: white; font-size: 14px; font-weight: 500;"
        )
        self.text_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.text_label)

        layout.addStretch()

    # -- animatable property (icon pixel size) --
    def _get_icon_px(self):
        return self._icon_px

    def _set_icon_px(self, value):
        self._icon_px = value
        self.icon_label.setPixmap(render_icon(self.icon_key, int(round(value))))

    icon_px = Property(float, _get_icon_px, _set_icon_px)

    def _animate_icon(self, target):
        anim = QPropertyAnimation(self, b"icon_px", self)
        anim.setDuration(ICON_ANIM_MS)
        anim.setStartValue(self._icon_px)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutBack)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._icon_anim = anim  # prevent garbage collection mid-animation

    def enterEvent(self, event):
        self._animate_icon(ICON_HOVER_PX)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_icon(ICON_BASE_PX)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)