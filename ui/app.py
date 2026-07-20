"""
Main app window. Assumes a user has already logged in/been created -
this file doesn't know or care about accounts, main.py handles that
and just hands MainWindow a username.

Layout:
  - Top bar: avatar + "Welcome, {name}" + Switch Account button
    (top-left cluster) + ACT logo (top-right)
  - Sidebar: two-panel, always-docked icon rail (ACT mark + icon-only
    nav) on the far left, plus a labeled panel that overlays the
    content area when the mouse hovers near the rail (or the panel
    itself), sliding away on mouse-leave after a short delay. Clicking
    a rail icon navigates immediately, hover is just for the labels.
  - Content area: stacked pages (Dashboard/Records/History/Late/Settings),
    switched via the sidebar, with a fade transition between them.
"""

import os

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame, QGraphicsDropShadowEffect
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QPointF, QRectF, QEvent, Signal, QSize, Property, QObject
from PySide6.QtGui import QPixmap, QColor, QPainter, QPen, QPainterPath

from ui.theme_manager import theme_manager
from ui.theme_utils import apply_live_style
from ui.nav_button import render_icon
from ui.avatar import Avatar
from ui.transition import FadeStackedWidget
from ui.notification_banner import NotificationBanner
from ui.notification_settings import notification_settings
from ui.Pages.Dashboard import DashboardPage
from ui.Pages.History import HistoryPage
from ui.Pages.Records import RecordsPage
from ui.Pages.Late import LatePage
from ui.Pages.Settings import SettingsPage
from storage_service import get_stale_status_counts

SIDEBAR_WIDTH = 150   # width of the labeled, hover-revealed panel
RAIL_WIDTH = 56        # width of the always-docked icon rail
TOP_BAR_HEIGHT = 68
HOVER_HIDE_DELAY_MS = 250
SIDEBAR_ANIM_MS = 200
NOTIFICATION_POLL_MS = 5 * 60 * 1000  # continuous background check, every 5 minutes

# Top-bar logo (top-right corner). Reuses the same asset as
# BootLogoSplash. Sized bigger/clearer per request - adjust if it ever
# looks cramped against TOP_BAR_HEIGHT.
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_TOPBAR_LOGO_PATH = os.path.join(_ASSETS_DIR, "logo.png")
TOPBAR_LOGO_TARGET_WIDTH = 130
RAIL_ICON_PX = 24  # bigger, crisp vector icon - was a 15px text glyph before
RAIL_ICON_ANIM_MS = 180
RAIL_LOGO_TARGET_WIDTH = 40  # real ACT logo, tinted white, sized to match the icons
SIDEBAR_OSMO_LOGO_TARGET_WIDTH = 62  # smaller than the rail mark, sits left-of-center in the panel
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

# Per-icon hover targets - each nav icon gets motion that matches what it
# represents, not one generic effect reused everywhere:
#   dashboard -> punchy scale "pop", like tiles snapping into place
#   records   -> smaller lift + a tilt, like picking a single page off a stack
#   export    -> swings counter-clockwise, like rewinding/turning back time
#   late      -> a shake, since this icon exists to flag something urgent
#   settings  -> a real gear-style rotation
_DASHBOARD_POP_SCALE = 1.22
_RECORDS_LIFT_SCALE = 1.12
_RECORDS_TILT_DEG = -8.0
_HISTORY_REWIND_DEG = -28.0
_SETTINGS_SPIN_DEG = 90.0
_LATE_POP_SCALE = 1.12

# Shared vertical rhythm for the rail and the panel: same top margin,
# same reserved header height, same row height, same spacing between
# rows. Building both columns from these exact numbers is what makes a
# panel label land level with its rail icon - not any kind of hand
# nudging per row.
RAIL_TOP_MARGIN = 16
HEADER_BLOCK_HEIGHT = 56
ROW_HEIGHT = 44        # matches IconRailButton's fixed size
ROW_SPACING = 8

# Purely decorative texture painted over the sliding Sidebar panel's
# orange background, so it reads as its own surface rather than a flat
# fill identical to the icon rail. Faint white diagonal hairlines -
# opacity kept low so it never competes with the labels on top.
_SIDEBAR_TEXTURE_COLOR = QColor(255, 255, 255, 16)
_SIDEBAR_TEXTURE_SPACING = 14
# A dark line drawn 1px below/right of each texture hairline before the
# light line itself, so each hairline reads as very slightly engraved
# instead of flat-printed on top of the orange.
_SIDEBAR_TEXTURE_SHADOW_COLOR = QColor(0, 0, 0, 30)
_SIDEBAR_TEXTURE_SHADOW_OFFSET = 1

# Rounded corners + drop shadow for the sliding Sidebar panel, and the
# "bubble" that pops in behind each PanelLabelRow on hover. All purely
# decorative - none of this changes sizing, positioning, or the
# show/hide/animate logic MainWindow already drives.
_SIDEBAR_CORNER_RADIUS = 14
_SIDEBAR_SHADOW_BLUR = 28
_SIDEBAR_SHADOW_OFFSET = (0, 4)
_SIDEBAR_SHADOW_COLOR = QColor(0, 0, 0, 90)

_BUBBLE_RADIUS = 8
_BUBBLE_IN_MS = 220
_BUBBLE_OUT_MS = 160


def _load_scaled_pixmap(path, target_width):
    """Load a pixmap and scale it to target_width, keeping aspect ratio.
    Shared by the top-bar logo and the rail logo mark so both always use
    the same real ACT logo asset rather than a placeholder."""
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return pixmap
    scaled_height = int(pixmap.height() * (target_width / pixmap.width()))
    return pixmap.scaled(
        target_width, scaled_height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _tint_white(pixmap):
    """Recolor every non-transparent pixel of pixmap to solid white,
    keeping its exact alpha shape. Used to put the real ACT logo (tick
    swoosh included) on the orange rail without needing a second,
    separately-exported white logo asset. Also used for the OSMO mark
    in the sidebar panel, same reasoning."""
    if pixmap.isNull():
        return pixmap
    tinted = QPixmap(pixmap.size())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), QColor("white"))
    painter.end()
    return tinted


def _find_osmo_logo_path():
    """Looks in assets/ for any image file with "osmo" in its name -
    deliberately not a fixed filename, so however it ends up named on
    disk, this still finds it."""
    if not os.path.isdir(_ASSETS_DIR):
        return None
    for name in sorted(os.listdir(_ASSETS_DIR)):
        lower = name.lower()
        if "osmo" in lower and lower.endswith(_IMAGE_EXTENSIONS):
            return os.path.join(_ASSETS_DIR, name)
    return None


class IconRailButton(QPushButton):
    """Icon-only nav button used in the always-visible rail. Clicking it
    navigates immediately - hovering the rail is only what reveals the
    labeled panel next to it, it isn't required to change pages.

    The icon is rendered once to a fixed-size base pixmap and then
    painted through a live QPainter transform (scale + rotation) rather
    than being re-rasterized at a different pixel size on every hover
    tick. That transform is what makes rotation possible at all - a
    plain resize-the-pixmap "pop" (the previous version of this class,
    and what ui/nav_button.py's NavButton still does) can only ever grow
    or shrink the icon uniformly, it can't tilt, swing, or shake it.
    """

    def __init__(self, icon_key, key, parent=None):
        super().__init__(parent)
        self.key = key
        self.icon_key = icon_key
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_pixmap = render_icon(icon_key, RAIL_ICON_PX)
        self._icon_scale = 1.0
        self._icon_rotation = 0.0
        # Set by MainWindow after both the rail and the labeled panel
        # exist (see _link_nav_hover) -- lets hovering this button's
        # PAIRED PanelLabelRow (same nav key, over in the other panel)
        # play this exact same animation, and lets moving directly
        # between the two count as one continuous hover instead of a
        # leave+re-enter. None until wired; enterEvent/leaveEvent fall
        # back to animating directly so this class still works standalone.
        self.hover_link = None
        apply_live_style(self, lambda c: f"""
            QPushButton {{
                border: none; border-radius: 10px; background: transparent;
            }}
            QPushButton:hover {{
                background-color: {QColor(c['ACCENT']).lighter(125).name()};
            }}
        """)

    # -- animatable properties (icon scale + rotation, degrees) --
    def _get_icon_scale(self):
        return self._icon_scale

    def _set_icon_scale(self, value):
        self._icon_scale = value
        self.update()

    icon_scale = Property(float, _get_icon_scale, _set_icon_scale)

    def _get_icon_rotation(self):
        return self._icon_rotation

    def _set_icon_rotation(self, value):
        self._icon_rotation = value
        self.update()

    icon_rotation = Property(float, _get_icon_rotation, _set_icon_rotation)

    def paintEvent(self, event):
        # Background/border/hover-highlight from the QSS above paints
        # first, exactly as before; the icon is then painted manually on
        # top through a transform instead of via setIcon(), since
        # QPushButton's own icon painting has no notion of rotation.
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self._icon_rotation)
        painter.scale(self._icon_scale, self._icon_scale)
        pixmap = self._icon_pixmap
        painter.drawPixmap(QPointF(-pixmap.width() / 2, -pixmap.height() / 2), pixmap)
        painter.end()

    def _animate_scale(self, target, easing, duration):
        anim = QPropertyAnimation(self, b"icon_scale", self)
        anim.setDuration(duration)
        anim.setStartValue(self._icon_scale)
        anim.setEndValue(target)
        anim.setEasingCurve(easing)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._scale_anim = anim  # prevent garbage collection mid-animation

    def _animate_rotation(self, target, easing, duration):
        anim = QPropertyAnimation(self, b"icon_rotation", self)
        anim.setDuration(duration)
        anim.setStartValue(self._icon_rotation)
        anim.setEndValue(target)
        anim.setEasingCurve(easing)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._rotation_anim = anim  # prevent garbage collection mid-animation

    def _play_shake(self):
        # Multi-keyframe rotation (not a simple start->end tween) so the
        # icon actually oscillates rather than moving to one tilted
        # position - fits "late" specifically, since this is the one
        # icon meant to read as urgent/attention-grabbing rather than
        # just responsive.
        anim = QPropertyAnimation(self, b"icon_rotation", self)
        anim.setDuration(380)
        anim.setKeyValueAt(0.0, 0.0)
        anim.setKeyValueAt(0.15, 12.0)
        anim.setKeyValueAt(0.35, -12.0)
        anim.setKeyValueAt(0.55, 7.0)
        anim.setKeyValueAt(0.75, -4.0)
        anim.setKeyValueAt(1.0, 0.0)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._rotation_anim = anim

    def play_hover_animation(self):
        if self.icon_key == "dashboard":
            self._animate_scale(_DASHBOARD_POP_SCALE, QEasingCurve.Type.OutBack, RAIL_ICON_ANIM_MS)
        elif self.icon_key == "records":
            self._animate_scale(_RECORDS_LIFT_SCALE, QEasingCurve.Type.OutCubic, RAIL_ICON_ANIM_MS)
            self._animate_rotation(_RECORDS_TILT_DEG, QEasingCurve.Type.OutBack, RAIL_ICON_ANIM_MS)
        elif self.icon_key == "export":
            self._animate_rotation(_HISTORY_REWIND_DEG, QEasingCurve.Type.OutBack, RAIL_ICON_ANIM_MS + 60)
        elif self.icon_key == "late":
            self._animate_scale(_LATE_POP_SCALE, QEasingCurve.Type.OutBack, 180)
            self._play_shake()
        elif self.icon_key == "settings":
            self._animate_rotation(_SETTINGS_SPIN_DEG, QEasingCurve.Type.InOutQuad, RAIL_ICON_ANIM_MS + 80)
        else:
            self._animate_scale(_DASHBOARD_POP_SCALE, QEasingCurve.Type.OutBack, RAIL_ICON_ANIM_MS)

    def play_leave_animation(self):
        # One generic reset back to baseline (scale 1, rotation 0) works
        # for every icon here regardless of which custom animation played
        # on the way in - none of the hover targets above are meant to
        # persist.
        self._animate_scale(1.0, QEasingCurve.Type.OutCubic, RAIL_ICON_ANIM_MS)
        self._animate_rotation(0.0, QEasingCurve.Type.OutCubic, RAIL_ICON_ANIM_MS)

    def enterEvent(self, event):
        if self.hover_link is not None:
            self.hover_link.enter()
        else:
            self.play_hover_animation()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.hover_link is not None:
            self.hover_link.leave()
        else:
            self.play_leave_animation()
        super().leaveEvent(event)


class _NavHoverLink(QObject):
    """Pairs one IconRailButton with its matching PanelLabelRow (same nav
    key, different panel) so hovering EITHER one plays the icon's
    animation, and moving directly from one to the other counts as one
    continuous hover rather than a leave-then-re-enter.

    Without this, since the rail and the labeled panel are two separate
    widgets sitting flush against each other, sliding the cursor from an
    icon to its own label fires that icon's leaveEvent immediately
    followed by the label's enterEvent -- which would snap the animation
    back to baseline and immediately replay it, a visible flicker/restart
    the animation was never meant to do mid-hover. leave() here doesn't
    commit right away; it waits a short beat, and enter() cancels that
    wait if it arrives first -- which is exactly what happens when the
    cursor crosses straight from one paired widget into the other.
    """

    _LEAVE_DELAY_MS = 80

    def __init__(self, icon_button):
        super().__init__()
        self.icon_button = icon_button
        self._active = False
        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.setInterval(self._LEAVE_DELAY_MS)
        self._leave_timer.timeout.connect(self._commit_leave)

    def enter(self):
        self._leave_timer.stop()
        if not self._active:
            self._active = True
            self.icon_button.play_hover_animation()

    def leave(self):
        self._leave_timer.start()

    def _commit_leave(self):
        self._active = False
        self.icon_button.play_leave_animation()


def _right_rounded_path(w, h, radius):
    """QPainterPath for a w x h rect with only the top-right and
    bottom-right corners rounded. Used for the fixed icon rail, whose
    left/top/bottom edges sit flush against the window frame and so
    shouldn't be rounded - only the edge facing the content area should
    read as a curve."""
    path = QPainterPath()
    path.moveTo(0, 0)
    path.lineTo(w - radius, 0)
    path.arcTo(w - 2 * radius, 0, 2 * radius, 2 * radius, 90, -90)
    path.lineTo(w, h - radius)
    path.arcTo(w - 2 * radius, h - 2 * radius, 2 * radius, 2 * radius, 0, -90)
    path.lineTo(0, h)
    path.closeSubpath()
    return path


class IconRail(QFrame):
    """Slim, always-docked strip: ACT logo mark on top, then icon-only
    versions of the same nav items as the labeled panel. No search icon -
    just the app's real sections. A slightly darker shade of the same
    orange as the panel, so the two read as one sidebar, not two colors."""

    def __init__(self, parent, nav_items, on_select):
        super().__init__(parent)
        self.setObjectName("iconRail")
        self.setFixedWidth(RAIL_WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # Background is hand-painted in paintEvent below (see there) so
        # its right edge can be genuinely curved - a plain stylesheet
        # fill would stay a square rect underneath any rounding drawn on
        # top of it. Still re-renders instantly on theme toggle, same as
        # the old apply_live_style version did.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        theme_manager.theme_changed.connect(lambda _mode=None: self.update())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, RAIL_TOP_MARGIN, 0, 16)
        layout.setSpacing(ROW_SPACING)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # Fixed-height header so the first icon always starts at
        # RAIL_TOP_MARGIN + HEADER_BLOCK_HEIGHT + ROW_SPACING, regardless
        # of the logo asset's actual pixel dimensions - the panel reuses
        # this exact same number for its own top spacer.
        header = QWidget()
        header.setFixedHeight(HEADER_BLOCK_HEIGHT)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        logo_label = QLabel()
        logo_label.setStyleSheet("background: transparent;")
        logo_pixmap = _tint_white(_load_scaled_pixmap(_TOPBAR_LOGO_PATH, RAIL_LOGO_TARGET_WIDTH))
        if not logo_pixmap.isNull():
            logo_label.setPixmap(logo_pixmap)
            logo_label.setFixedSize(logo_pixmap.size())
        header_layout.addWidget(logo_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(header)

        self.buttons = {}
        for icon_key, _label, key in nav_items:
            btn = IconRailButton(icon_key, key)
            btn.clicked.connect(lambda checked=False, k=key: on_select(k))
            layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)
            self.buttons[key] = btn

        layout.addStretch()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = _right_rounded_path(self.width(), self.height(), _SIDEBAR_CORNER_RADIUS)
        fill = QColor(theme_manager.colors()["ACCENT"]).darker(112)
        painter.setClipPath(path)
        painter.fillPath(path, fill)
        painter.end()


class PanelLabelRow(QFrame):
    """One row in the expanded panel: label only, no icon - the icon
    already lives in the rail immediately to this row's left. Fixed at
    ROW_HEIGHT so, combined with the same top margin/header height/
    spacing as the rail, this row's vertical center always lands
    exactly level with its icon in the rail."""
    clicked = Signal(str)

    def __init__(self, key, label, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("panelLabelRow")
        self.setFixedHeight(ROW_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Set by MainWindow after both panels exist (see
        # _link_nav_hover) -- see _NavHoverLink for why hovering a name
        # plays its paired icon's animation instead of the label doing
        # anything on its own.
        self.hover_link = None
        # Animated 0.0-1.0 value driving the hover "bubble" - see
        # bubble_scale property + paintEvent below. Kept separate from
        # hover_link, which only ever triggers the paired rail icon's
        # animation, not this row's own visuals.
        self._bubble_scale = 0.0
        self._bubble_anim = None

        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(14, 0, 14, 0)

        self.text_label = QLabel(label)
        self.text_label.setStyleSheet(
            "background: transparent; color: white; font-size: 14px; font-weight: 500;"
        )
        row_layout.addWidget(self.text_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        row_layout.addStretch()

        apply_live_style(self, lambda c: "QFrame#panelLabelRow { background: transparent; }")

    # -- animatable property: how "popped in" the hover bubble is --
    def _get_bubble_scale(self):
        return self._bubble_scale

    def _set_bubble_scale(self, value):
        self._bubble_scale = value
        self.update()

    bubble_scale = Property(float, _get_bubble_scale, _set_bubble_scale)

    def _animate_bubble(self, target):
        anim = QPropertyAnimation(self, b"bubble_scale", self)
        anim.setStartValue(self._bubble_scale)
        anim.setEndValue(target)
        if target > 0:
            anim.setDuration(_BUBBLE_IN_MS)
            anim.setEasingCurve(QEasingCurve.Type.OutBack)
        else:
            anim.setDuration(_BUBBLE_OUT_MS)
            anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._bubble_anim = anim  # prevent garbage collection mid-animation

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._bubble_scale <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = self.rect()
        w = full.width() * self._bubble_scale
        h = full.height() * self._bubble_scale
        bubble_rect = QRectF(
            full.center().x() - w / 2, full.center().y() - h / 2, w, h
        )
        fill = QColor(theme_manager.colors()["ACCENT"]).lighter(120)
        fill.setAlphaF(min(1.0, 0.35 + 0.55 * self._bubble_scale))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(bubble_rect, _BUBBLE_RADIUS, _BUBBLE_RADIUS)
        painter.end()

    def mousePressEvent(self, event):
        self.clicked.emit(self.key)
        super().mousePressEvent(event)

    def enterEvent(self, event):
        if self.hover_link is not None:
            self.hover_link.enter()
        self._animate_bubble(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.hover_link is not None:
            self.hover_link.leave()
        self._animate_bubble(0.0)
        super().leaveEvent(event)


class Sidebar(QFrame):
    """Solid-orange labeled panel - the second half of the two-panel
    sidebar, docked icon rail being the first. Positioned/animated by
    MainWindow, not by a layout, since it needs to overlay the content
    area (immediately right of the rail) rather than push it. Holds
    label-only rows built from the same RAIL_TOP_MARGIN/HEADER_BLOCK_
    HEIGHT/ROW_HEIGHT/ROW_SPACING constants as IconRail, so every label
    lines up with its icon instead of drifting out of alignment."""

    def __init__(self, parent, nav_items, on_select):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)
        self.on_select = on_select
        self.buttons = {}

        # Corners are rounded by hand in paintEvent below (see there for
        # why), so the widget itself needs to allow per-pixel transparency
        # instead of always being treated as a fully opaque rectangle.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(_SIDEBAR_SHADOW_BLUR)
        shadow.setOffset(*_SIDEBAR_SHADOW_OFFSET)
        shadow.setColor(_SIDEBAR_SHADOW_COLOR)
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, RAIL_TOP_MARGIN, 0, 16)
        layout.setSpacing(ROW_SPACING)

        # Header block matching the rail's header height exactly, so the
        # first label starts at the same y as the first icon - holds the
        # OSMO mark (tinted white, like the rail's ACT mark) instead of
        # a blank spacer. Only shows/slides in when the panel itself
        # does, since it lives in this panel rather than the always-
        # docked rail.
        header = QWidget()
        header.setFixedHeight(HEADER_BLOCK_HEIGHT)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 0, 0, 0)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        osmo_logo_label = QLabel()
        osmo_logo_label.setStyleSheet("background: transparent;")
        osmo_logo_path = _find_osmo_logo_path()
        if osmo_logo_path:
            osmo_pixmap = _tint_white(_load_scaled_pixmap(osmo_logo_path, SIDEBAR_OSMO_LOGO_TARGET_WIDTH))
            if not osmo_pixmap.isNull():
                osmo_logo_label.setPixmap(osmo_pixmap)
                osmo_logo_label.setFixedSize(osmo_pixmap.size())
        header_layout.addWidget(osmo_logo_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        header_layout.addStretch()
        layout.addWidget(header)

        for _icon_key, label, key in nav_items:
            row = PanelLabelRow(key, label)
            row.clicked.connect(self.on_select)
            layout.addWidget(row)
            self.buttons[key] = row

        layout.addStretch()

    def paintEvent(self, event):
        # Paints the panel itself instead of relying on the #sidebar QSS
        # rule in theme.py, so the corners can be genuinely rounded (a
        # stylesheet border-radius on an opaque widget still leaves
        # square corners underneath) and the shadow effect above has real
        # transparent pixels outside the rounded shape to follow. The
        # fill color still comes from the same ACCENT theme color as
        # before - only how it's painted has changed.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()), _SIDEBAR_CORNER_RADIUS, _SIDEBAR_CORNER_RADIUS
        )
        painter.setClipPath(path)
        painter.fillPath(path, QColor(theme_manager.colors()["ACCENT"]))

        # Faint diagonal hairline texture on top, clipped to the same
        # rounded shape so it never pokes out past the corners. Each
        # hairline gets a 1px dark "shadow" line just below/right of it
        # first, then the light line drawn on top - reads as a slight
        # groove rather than a flat white line sitting on the orange.
        shadow_pen = QPen(_SIDEBAR_TEXTURE_SHADOW_COLOR)
        shadow_pen.setWidth(1)
        highlight_pen = QPen(_SIDEBAR_TEXTURE_COLOR)
        highlight_pen.setWidth(1)
        offset = _SIDEBAR_TEXTURE_SHADOW_OFFSET
        w, h = self.width(), self.height()
        x = -h
        while x < w:
            painter.setPen(shadow_pen)
            painter.drawLine(x + offset, h + offset, x + h + offset, offset)
            painter.setPen(highlight_pen)
            painter.drawLine(x, h, x + h, 0)
            x += _SIDEBAR_TEXTURE_SPACING
        painter.end()


class MainWindow(QWidget):
    switch_account_requested = Signal()

    def __init__(self, user_name):
        super().__init__()
        self.user_name = user_name
        self.setObjectName("mainWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._sidebar_visible = False
        self.sidebar = None  # set in _build_ui; must exist before any events can fire
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_sidebar)

        self._build_ui()

        self._notify_timer = QTimer(self)
        self._notify_timer.timeout.connect(self._check_notifications)
        self._notify_timer.start(NOTIFICATION_POLL_MS)
        self._check_notifications()

    # -----------------------------------------------------------------
    # Layout
    # -----------------------------------------------------------------
    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_top_bar())

        separator = QFrame()
        separator.setFixedHeight(1)
        apply_live_style(separator, lambda c: f"background-color: {c['BORDER']};")
        root_layout.addWidget(separator)

        self.notification_banner = NotificationBanner(self)
        self.notification_banner.view_requested.connect(self._on_notification_view)
        root_layout.addWidget(self.notification_banner)

        body_layout = QHBoxLayout()
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        root_layout.addLayout(body_layout, stretch=1)

        content_area = QWidget()
        self._content_area = content_area

        self.stack = FadeStackedWidget(content_area)
        self.pages = {
            "Dashboard": DashboardPage(),
            "Records": RecordsPage(),
            "History": HistoryPage(),
            "Late": LatePage(),
            "Settings": SettingsPage(),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        # Icons are real SVGs (see ui/nav_button.py's _ICONS), not
        # Unicode glyphs - a glyph like the old "⚠" gets hijacked by
        # whatever color emoji font the OS ships (that's why the warning
        # triangle used to render yellow). Each icon key below maps to a
        # vector icon chosen for what the label actually means, not just
        # a generic shape.
        nav_items = [
            ("dashboard", "Dashboard", "Dashboard"),
            ("records", "Records", "Records"),
            ("export", "Export History", "History"),
            ("late", "Late", "Late"),
            ("settings", "Settings", "Settings"),
        ]
        self.icon_rail = IconRail(self, nav_items, self._on_nav_select)
        self.icon_rail.installEventFilter(self)
        body_layout.addWidget(self.icon_rail)
        body_layout.addWidget(content_area, stretch=1)

        self.sidebar = Sidebar(content_area, nav_items, self._on_nav_select)
        self.sidebar.installEventFilter(self)
        self.sidebar.move(-SIDEBAR_WIDTH, 0)
        self.sidebar.raise_()

        self._link_nav_hover()

        # Reposition sidebar/stack whenever the content area resizes.
        content_area.resizeEvent = self._on_content_resize

        self.show_page("Dashboard")

    def _build_top_bar(self):
        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        top_bar.setFixedHeight(TOP_BAR_HEIGHT)

        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 0, 16, 0)
        top_layout.setSpacing(0)

        # Clickable avatar: click for View/Choose photo.../Remove photo.
        # Falls back to initials on an orange circle if no photo is set.
        avatar = Avatar(self.user_name, size=44)
        top_layout.addWidget(avatar)
        top_layout.addSpacing(12)

        welcome = QLabel(f"Welcome, {self.user_name}")
        apply_live_style(welcome, lambda c: (
            f"font-size: 20px; font-weight: 700; color: {c['TEXT_PRIMARY']};"
        ))
        top_layout.addWidget(welcome)
        top_layout.addSpacing(16)

        # Switch Account now sits next to the name (left cluster), instead
        # of the far right - connection/behavior is unchanged.
        switch_account_btn = QPushButton("⇄ Switch Account")
        switch_account_btn.setObjectName("secondaryButton")
        switch_account_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        switch_account_btn.clicked.connect(self.switch_account_requested.emit)
        top_layout.addWidget(switch_account_btn)

        top_layout.addStretch()

        # ACT logo - top-right corner, bigger/clearer. Stored as
        # self.topbar_logo_label (not a local var) so RootWindow
        # (main.py) can find its on-screen position and animate a
        # matching logo into this exact spot during the welcome ->
        # main-window handoff, without this file needing to know
        # anything about that animation.
        self.topbar_logo_label = QLabel()
        logo_pixmap = self._load_topbar_logo_pixmap()
        self.topbar_logo_label.setPixmap(logo_pixmap)
        self.topbar_logo_label.setFixedSize(logo_pixmap.size())
        top_layout.addWidget(self.topbar_logo_label, alignment=Qt.AlignmentFlag.AlignVCenter)

        return top_bar

    @staticmethod
    def _load_topbar_logo_pixmap():
        # Same asset/loading approach as BootLogoSplash, just scaled to
        # fit the top bar.
        return _load_scaled_pixmap(_TOPBAR_LOGO_PATH, TOPBAR_LOGO_TARGET_WIDTH)

    # -----------------------------------------------------------------
    # Sidebar positioning + hover show/hide
    # -----------------------------------------------------------------
    def _on_content_resize(self, event):
        h = self._content_area.height()
        w = self._content_area.width()
        self.stack.setGeometry(0, 0, w, h)
        x = 0 if self._sidebar_visible else -SIDEBAR_WIDTH
        self.sidebar.setGeometry(x, 0, SIDEBAR_WIDTH, h)

    def eventFilter(self, obj, event):
        if obj in (self.icon_rail, self.sidebar):
            if event.type() == QEvent.Type.Enter:
                self._hide_timer.stop()
                self._show_sidebar()
            elif event.type() == QEvent.Type.Leave:
                self._hide_timer.start(HOVER_HIDE_DELAY_MS)
        return super().eventFilter(obj, event)

    def _show_sidebar(self):
        if self._sidebar_visible:
            return
        self._sidebar_visible = True
        self._animate_sidebar(0)

    def _hide_sidebar(self):
        if not self._sidebar_visible:
            return
        self._sidebar_visible = False
        self._animate_sidebar(-SIDEBAR_WIDTH)

    def _animate_sidebar(self, target_x):
        anim = QPropertyAnimation(self.sidebar, b"pos", self)
        anim.setDuration(SIDEBAR_ANIM_MS)
        anim.setEndValue(QPoint(target_x, 0))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._sidebar_anim = anim  # prevent garbage collection mid-animation

    # -----------------------------------------------------------------
    # Page routing
    # -----------------------------------------------------------------
    def _link_nav_hover(self):
        """One _NavHoverLink per nav key, shared by that key's
        IconRailButton (in self.icon_rail) and PanelLabelRow (in
        self.sidebar) -- see _NavHoverLink for why hovering either one
        plays the icon's animation, with no flicker when the cursor
        crosses directly between the two."""
        for key, icon_button in self.icon_rail.buttons.items():
            link = _NavHoverLink(icon_button)
            icon_button.hover_link = link
            label_row = self.sidebar.buttons.get(key)
            if label_row is not None:
                label_row.hover_link = link

    def _on_nav_select(self, key):
        self.show_page(key)

    def show_page(self, key):
        self.stack.setCurrentWidget(self.pages[key])

    # -----------------------------------------------------------------
    # Stale pending/rejected notifications
    # -----------------------------------------------------------------
    def _check_notifications(self):
        if not notification_settings.enabled:
            self.notification_banner.hide()
            return

        counts = get_stale_status_counts(notification_settings.threshold_hours)
        total = counts.get("total", 0)
        if total <= 0:
            self.notification_banner.hide()
            return

        threshold_text = self._format_threshold(notification_settings.threshold_hours)
        message = (
            f"{total} request{'s' if total != 1 else ''} waiting over {threshold_text} "
            f"(pending: {counts.get('pending', 0)}, rejected: {counts.get('rejected', 0)})"
        )
        self.notification_banner.show_message(message)

    @staticmethod
    def _format_threshold(hours):
        if hours >= 24 and hours % 24 == 0:
            days = int(hours // 24)
            return f"{days} day{'s' if days != 1 else ''}"
        return f"{int(hours)} hour{'s' if int(hours) != 1 else ''}"

    def _on_notification_view(self):
        self.show_page("Late")