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
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QEvent

from ui.theme_manager import theme_manager
from ui.theme_utils import apply_live_style
from ui.nav_button import NavButton
from ui.transition import FadeStackedWidget
from ui.Pages.Dashboard import DashboardPage
from ui.Pages.History import HistoryPage
from ui.Pages.placeholder import PlaceholderPage
from ui.Pages.Settings import SettingsPage

SIDEBAR_WIDTH = 150
TOP_BAR_HEIGHT = 56
HOVER_HIDE_DELAY_MS = 250
SIDEBAR_ANIM_MS = 200


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

        content_area = QWidget()
        root_layout.addWidget(content_area, stretch=1)
        self._content_area = content_area

        self.stack = FadeStackedWidget(content_area)
        self.pages = {
            "Dashboard": DashboardPage(),
            "Scan": PlaceholderPage("Scan Inbox"),
            "Records": PlaceholderPage("Records"),
            "History": PlaceholderPage("Export History"),
            "Calendar": PlaceholderPage("Calendar"),
            "Settings": SettingsPage(),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        # icon glyphs are plain Unicode symbols (no extra dependency needed).
        # For icons closer to a proper icon set, the "qtawesome" pip package
        # is a natural upgrade later - say the word.
        # \uFE0E forces "text" (monochrome/flat) presentation instead of
        # the default full-color emoji glyph some fonts use for these two
        # in particular - without it, Calendar/Settings look visually
        # inconsistent with the rest of the flat sidebar icons.
        nav_items = [
            ("\u25A3", "Dashboard", "Dashboard"),
            ("\u2709", "Scan Inbox", "Scan"),
            ("\u25A4", "Records", "Records"),
            ("\u21BA", "Export History", "History"),
            ("\U0001F5D3\uFE0E", "Calendar", "Calendar"),
            ("\u2699\uFE0E", "Settings", "Settings"),
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