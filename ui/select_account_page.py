"""
Select-account page - shown on every launch after the very first account
exists. Tapping a tile logs straight in (no password re-entry, per the
agreed "switch users" style flow). An "Add account" tile is always shown
alongside existing accounts so more can be created later.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel, QFrame
from PySide6.QtCore import Qt, Signal

from ui.athu import list_accounts
from ui.theme import (
    COLOR_BG, COLOR_ACCENT, COLOR_ACCENT_LIGHT, COLOR_TEXT_PRIMARY,
    COLOR_TEXT_ON_ACCENT, COLOR_BORDER,
)

TILE_SIZE = (140, 160)
COLUMNS = 4


class AccountTile(QFrame):
    clicked = Signal(str)

    def __init__(self, username, is_add_tile=False):
        super().__init__()
        self.username = username
        self.is_add_tile = is_add_tile
        self.setFixedSize(*TILE_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(8)

        avatar = QLabel("+" if self.is_add_tile else self._initials())
        avatar.setFixedSize(64, 64)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bg = COLOR_ACCENT_LIGHT if self.is_add_tile else COLOR_ACCENT
        fg = COLOR_ACCENT if self.is_add_tile else COLOR_TEXT_ON_ACCENT
        avatar.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border-radius: 32px; "
            f"font-size: 22px; font-weight: 700;"
        )
        layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignCenter)

        name_label = QLabel("Add account" if self.is_add_tile else self.username)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 13px;")
        layout.addWidget(name_label)

        self.setStyleSheet(f"""
            AccountTile {{
                background-color: white;
                border: 1px solid {COLOR_BORDER};
                border-radius: 12px;
            }}
            AccountTile:hover {{
                border: 1px solid {COLOR_ACCENT};
            }}
        """)

    def _initials(self):
        parts = self.username.split()
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    def mousePressEvent(self, event):
        self.clicked.emit(self.username)
        super().mousePressEvent(event)


class SelectAccountPage(QWidget):
    account_selected = Signal(str)
    add_account_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {COLOR_BG};")
        self._outer = QVBoxLayout(self)
        self._outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._outer.setSpacing(20)
        self._grid_container = None
        self._build_ui()

    def _build_ui(self):
        title = QLabel("Who's using the app?")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        self._outer.addWidget(title)
        # Grid is intentionally NOT built here - showEvent() builds it fresh
        # every time this page is shown, so it's never stale or duplicated.

    def _rebuild_grid(self):
        if self._grid_container is not None:
            self._outer.removeWidget(self._grid_container)
            self._grid_container.deleteLater()

        self._grid_container = QWidget()
        grid = QGridLayout(self._grid_container)
        grid.setSpacing(16)

        accounts = list_accounts()
        for i, account in enumerate(accounts):
            tile = AccountTile(account.username)
            tile.clicked.connect(self.account_selected.emit)
            grid.addWidget(tile, i // COLUMNS, i % COLUMNS)

        add_tile = AccountTile("", is_add_tile=True)
        add_tile.clicked.connect(lambda _u: self.add_account_requested.emit())
        grid.addWidget(add_tile, len(accounts) // COLUMNS, len(accounts) % COLUMNS)

        self._outer.addWidget(self._grid_container, alignment=Qt.AlignmentFlag.AlignCenter)

    def showEvent(self, event):
        # Refresh the tile list every time this page is shown, so a
        # freshly-added account actually appears without restarting the app.
        self._rebuild_grid()
        super().showEvent(event)