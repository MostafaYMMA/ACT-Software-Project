"""
Main app window. Assumes a user has already logged in/been created -
this file doesn't know or care about accounts, main.py handles that
and just hands MainWindow a username.

Layout:
  - Top bar: hamburger icon (top-left) + avatar + "Welcome, {name}"
  - Sidebar: hidden by default, overlays the content area when the
    mouse hovers near the hamburger icon (or the sidebar itself),
    slides away on mouse-leave after a short delay.
  - Content area: stacked pages (Dashboard/Scan/Records/History/Settings),
    switched via the sidebar, with a fade transition between them.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QEvent

from ui.theme import (
    COLOR_BG, COLOR_ACCENT, COLOR_ACCENT_DARK, COLOR_ACCENT_LIGHT,
    COLOR_TEXT_PRIMARY, COLOR_TEXT_ON_ACCENT, COLOR_BORDER,
)
from ui.transition import FadeStackedWidget
from ui.Pages.Dashboard import DashboardPage
from ui.Pages.placeholder import PlaceholderPage

SIDEBAR_WIDTH = 150
TOP_BAR_HEIGHT = 56
HOVER_HIDE_DELAY_MS = 250
SIDEBAR_ANIM_MS = 200


class Sidebar(QFrame):
    """Solid-orange nav drawer. Positioned/animated by MainWindow, not by
    a layout, since it needs to overlay the content area rather than push it."""

    def __init__(self, parent, nav_items, on_select):
        super().__init__(parent)
        self.setFixedWidth(SIDEBAR_WIDTH)
        self.setStyleSheet(f"background-color: {COLOR_ACCENT}; border: none;")
        self.on_select = on_select
        self.buttons = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 24, 0, 0)
        layout.setSpacing(2)

        for label, key in nav_items:
            btn = QPushButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    text-align: left; padding: 12px 24px; border: none;
                    color: {COLOR_TEXT_ON_ACCENT}; background: transparent; font-size: 16px;
                }}
                QPushButton:hover {{ background-color: {COLOR_ACCENT_DARK}; }}
            """)
            btn.clicked.connect(lambda checked=False, k=key: self.on_select(k))
            layout.addWidget(btn)
            self.buttons[key] = btn

        layout.addStretch()


class MainWindow(QWidget):
    def __init__(self, user_name):
        super().__init__()
        self.user_name = user_name
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLOR_BG};")

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
        separator.setStyleSheet(f"background-color: {COLOR_BORDER};")
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
            "Settings": PlaceholderPage("Settings"),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        nav_items = [
            ("Dashboard", "Dashboard"),
            ("Scan Inbox", "Scan"),
            ("Records", "Records"),
            ("Export History", "History"),
            ("Settings", "Settings"),
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
        top_bar.setFixedHeight(TOP_BAR_HEIGHT)
        top_bar.setStyleSheet("background-color: white;")

        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 0, 16, 0)
        top_layout.setSpacing(0)

        self.hamburger_btn = QPushButton("\u2630")
        self.hamburger_btn.setFixedSize(36, 36)
        self.hamburger_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hamburger_btn.setStyleSheet(f"""
            QPushButton {{
                border: none; font-size: 18px; color: {COLOR_ACCENT}; background: transparent;
            }}
            QPushButton:hover {{ background-color: {COLOR_ACCENT_LIGHT}; border-radius: 6px; }}
        """)
        self.hamburger_btn.installEventFilter(self)
        top_layout.addWidget(self.hamburger_btn)
        top_layout.addSpacing(12)

        avatar = QLabel(self._initials())
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            f"background-color: {COLOR_ACCENT}; color: {COLOR_TEXT_ON_ACCENT}; "
            f"border-radius: 16px; font-weight: 700; font-size: 12px;"
        )
        top_layout.addWidget(avatar)
        top_layout.addSpacing(10)

        welcome = QLabel(f"Welcome, {self.user_name}")
        welcome.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
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