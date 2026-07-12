"""
Main app window. Assumes a user has already logged in/been created -
this file doesn't know or care about accounts, main.py handles that
and just hands MainWindow a username.

Layout:
  - Top bar: hamburger icon (top-left) + avatar + "Welcome, {name}"
  - Sidebar: hidden by default, overlays the content area when the
    mouse hovers near the hamburger icon (or the sidebar itself),
    slides away on mouse-leave after a short delay.
  - Content area: stacked pages (Dashboard/Scan/Records/History/Calendar/
    Settings), switched via the sidebar, with a fade transition between them.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QEvent, Signal

from ui.theme_manager import theme_manager
from ui.theme_utils import apply_live_style
from ui.nav_button import NavButton
from ui.transition import FadeStackedWidget
from ui.notification_banner import NotificationBanner
from ui.notification_settings import notification_settings
from ui.Pages.Dashboard import DashboardPage
from ui.Pages.History import HistoryPage
from ui.Pages.Records import RecordsPage
from ui.Pages.placeholder import PlaceholderPage
from ui.Pages.Settings import SettingsPage
from storage_service import get_stale_status_counts

SIDEBAR_WIDTH = 150
TOP_BAR_HEIGHT = 56
HOVER_HIDE_DELAY_MS = 250
SIDEBAR_ANIM_MS = 200
NOTIFICATION_POLL_MS = 5 * 60 * 1000  # continuous background check, every 5 minutes


class Sidebar(QFrame):
    """Solid-orange nav drawer. Positioned/animated by MainWindow, not by
    a layout, since it needs to overlay the content area rather than push it.
    Colors come from the global stylesheet (see ui/theme.py #sidebar /
    #navButton selectors) so light/dark mode recolors this live."""

    def __init__(self, parent, nav_items, on_select):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)
        self.on_select = on_select
        self.buttons = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 24, 0, 0)
        layout.setSpacing(2)

        for icon, label, key in nav_items:
            btn = NavButton(icon, label)
            btn.clicked.connect(lambda k=key: self.on_select(k))
            layout.addWidget(btn)
            self.buttons[key] = btn

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

        content_area = QWidget()
        root_layout.addWidget(content_area, stretch=1)
        self._content_area = content_area

        self.stack = FadeStackedWidget(content_area)
        self.pages = {
            "Dashboard": DashboardPage(),
            "Scan": PlaceholderPage("Scan Inbox"),
            "Records": RecordsPage(),
            "History": HistoryPage(),
            "Calendar": PlaceholderPage("Calendar"),
            "Settings": SettingsPage(),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        # icon glyphs are plain Unicode symbols (no extra dependency needed).
        # For icons closer to a proper icon set, the "qtawesome" pip package
        # is a natural upgrade later - say the word.
        # All non-semantic icons (Dashboard/Records/History/Calendar) are
        # deliberately drawn from the same Geometric Shapes block so they
        # share the same visual weight - mixing that with color emoji
        # (e.g. a calendar pictograph) reads as inconsistent in the sidebar.
        nav_items = [
            ("\u25A3", "Dashboard", "Dashboard"),
            ("\u2709", "Scan Inbox", "Scan"),
            ("\u25A4", "Records", "Records"),
            ("\u21BA", "Export History", "History"),
            ("\u25A6", "Calendar", "Calendar"),
            ("\u2699", "Settings", "Settings"),
        ]
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

        self.hamburger_btn = QPushButton("\u2630")
        self.hamburger_btn.setFixedSize(36, 36)
        self.hamburger_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_live_style(self.hamburger_btn, lambda c: f"""
            QPushButton {{
                border: none; font-size: 18px; color: {c['ACCENT']}; background: transparent;
            }}
            QPushButton:hover {{ background-color: {c['ACCENT_LIGHT']}; border-radius: 6px; }}
        """)
        self.hamburger_btn.installEventFilter(self)
        top_layout.addWidget(self.hamburger_btn)
        top_layout.addSpacing(12)

        avatar = QLabel(self._initials())
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # ACCENT and TEXT_ON_ACCENT happen to be identical in both palettes
        # right now, but going through theme_manager keeps this correct
        # even if that ever changes.
        apply_live_style(avatar, lambda c: (
            f"background-color: {c['ACCENT']}; color: {c['TEXT_ON_ACCENT']}; "
            f"border-radius: 16px; font-weight: 700; font-size: 12px;"
        ))
        top_layout.addWidget(avatar)
        top_layout.addSpacing(10)

        welcome = QLabel(f"Welcome, {self.user_name}")
        apply_live_style(welcome, lambda c: (
            f"font-size: 15px; font-weight: 700; color: {c['TEXT_PRIMARY']};"
        ))
        top_layout.addWidget(welcome)

        top_layout.addStretch()

        switch_account_btn = QPushButton("⇄ Switch Account")
        switch_account_btn.setObjectName("secondaryButton")
        switch_account_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        switch_account_btn.clicked.connect(self.switch_account_requested.emit)
        top_layout.addWidget(switch_account_btn)

        return top_bar

    def _initials(self):
        parts = self.user_name.split()
        return "".join(p[0] for p in parts[:2]).upper() or "?"

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
        if obj in (self.hamburger_btn, self.sidebar):
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

        self._last_stale_counts = counts
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
        counts = getattr(self, "_last_stale_counts", {})
        preferred = "pending" if counts.get("pending", 0) > 0 else "reject"
        self.show_page("Dashboard")
        self.pages["Dashboard"].select_stat_card(preferred)