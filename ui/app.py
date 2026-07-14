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

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QEvent, Signal, QSize
from PySide6.QtGui import QPixmap, QColor, QIcon, QPainter

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
RAIL_LOGO_TARGET_WIDTH = 40  # real ACT logo, tinted white, sized to match the icons

# Shared vertical rhythm for the rail and the panel: same top margin,
# same reserved header height, same row height, same spacing between
# rows. Building both columns from these exact numbers is what makes a
# panel label land level with its rail icon - not any kind of hand
# nudging per row.
RAIL_TOP_MARGIN = 16
HEADER_BLOCK_HEIGHT = 56
ROW_HEIGHT = 44        # matches IconRailButton's fixed size
ROW_SPACING = 8


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
    separately-exported white logo asset."""
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


class IconRailButton(QPushButton):
    """Icon-only nav button used in the always-visible rail. Clicking it
    navigates immediately - hovering the rail is only what reveals the
    labeled panel next to it, it isn't required to change pages."""

    def __init__(self, icon_key, key, parent=None):
        super().__init__(parent)
        self.key = key
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIcon(QIcon(render_icon(icon_key, RAIL_ICON_PX)))
        self.setIconSize(QSize(RAIL_ICON_PX, RAIL_ICON_PX))
        apply_live_style(self, lambda c: f"""
            QPushButton {{
                border: none; border-radius: 10px; background: transparent;
            }}
            QPushButton:hover {{
                background-color: {QColor(c['ACCENT']).lighter(125).name()};
            }}
        """)


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
        apply_live_style(self, lambda c: f"background-color: {QColor(c['ACCENT']).darker(112).name()};")

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

        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(14, 0, 14, 0)

        self.text_label = QLabel(label)
        self.text_label.setStyleSheet(
            "background: transparent; color: white; font-size: 14px; font-weight: 500;"
        )
        row_layout.addWidget(self.text_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        row_layout.addStretch()

        apply_live_style(self, lambda c: f"""
            QFrame#panelLabelRow {{ background: transparent; border-radius: 8px; }}
            QFrame#panelLabelRow:hover {{ background-color: {QColor(c['ACCENT']).lighter(115).name()}; }}
        """)

    def mousePressEvent(self, event):
        self.clicked.emit(self.key)
        super().mousePressEvent(event)


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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, RAIL_TOP_MARGIN, 0, 16)
        layout.setSpacing(ROW_SPACING)

        # Empty spacer matching the rail's header block height exactly,
        # so the first label starts at the same y as the first icon.
        header_spacer = QWidget()
        header_spacer.setFixedHeight(HEADER_BLOCK_HEIGHT)
        layout.addWidget(header_spacer)

        for _icon_key, label, key in nav_items:
            row = PanelLabelRow(key, label)
            row.clicked.connect(self.on_select)
            layout.addWidget(row)
            self.buttons[key] = row

        layout.addStretch()


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