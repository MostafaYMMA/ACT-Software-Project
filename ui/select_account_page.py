"""
Select-account page - shown on every launch after the very first account
exists. Selecting an existing account requires re-entering that account's
password before login continues.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QFrame, QLineEdit, QPushButton,
)
from PySide6.QtCore import Qt, Signal

from ui.athu import list_accounts, verify_password
from ui.profile_circle import ProfileCircle
from ui.theme import (
    COLOR_BG, COLOR_ACCENT, COLOR_ACCENT_LIGHT, COLOR_TEXT_PRIMARY,
    COLOR_BORDER, COLOR_ERROR,
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

        if self.is_add_tile:
            avatar = QLabel("+")
            avatar.setFixedSize(64, 64)
            avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            avatar.setStyleSheet(
                f"background-color: {COLOR_ACCENT_LIGHT}; color: {COLOR_ACCENT}; "
                f"border-radius: 32px; font-size: 22px; font-weight: 700;"
            )
        else:
            # Shows the account's chosen photo if one was set via the
            # top-bar Avatar, otherwise falls back to initials - same
            # shared widget/settings key as ui/avatar.py.
            avatar = ProfileCircle(self.username, size=64)
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
        self._pending_username = None
        self._build_ui()

    def _build_ui(self):
        title = QLabel("Who's using the app?")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        self._outer.addWidget(title)

        self._password_card = QFrame()
        self._password_card.setFixedWidth(320)
        password_layout = QVBoxLayout(self._password_card)
        password_layout.setSpacing(8)

        self._password_label = QLabel("Enter password to continue")
        self._password_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._password_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 13px;")
        password_layout.addWidget(self._password_label)

        self._password_input = QLineEdit()
        self._password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_input.setPlaceholderText("Password")
        self._password_input.returnPressed.connect(self._confirm_password)
        password_layout.addWidget(self._password_input)

        self._password_error = QLabel("")
        self._password_error.setStyleSheet(f"color: {COLOR_ERROR}; font-size: 11px;")
        self._password_error.setWordWrap(True)
        self._password_error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        password_layout.addWidget(self._password_error)

        self._confirm_password_btn = QPushButton("Continue")
        self._confirm_password_btn.clicked.connect(self._confirm_password)
        password_layout.addWidget(self._confirm_password_btn)

        self._cancel_password_btn = QPushButton("Back")
        self._cancel_password_btn.clicked.connect(self._cancel_password)
        password_layout.addWidget(self._cancel_password_btn)

        self._password_card.hide()
        self._outer.addWidget(self._password_card)
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
            tile.clicked.connect(self._on_tile_clicked)
            grid.addWidget(tile, i // COLUMNS, i % COLUMNS)

        add_tile = AccountTile("", is_add_tile=True)
        add_tile.clicked.connect(lambda _u: self.add_account_requested.emit())
        grid.addWidget(add_tile, len(accounts) // COLUMNS, len(accounts) % COLUMNS)

        self._outer.addWidget(self._grid_container, alignment=Qt.AlignmentFlag.AlignCenter)

    def _on_tile_clicked(self, username):
        # Every tile click - even for an account the user just used a
        # moment ago - must re-prove the password before continuing.
        self._pending_username = username
        self._password_label.setText(f"Enter password for {username}")
        self._password_input.clear()
        self._password_error.setText("")
        self._password_card.show()
        self._password_input.setFocus()

        if self._grid_container is not None:
            self._grid_container.hide()

    def _confirm_password(self):
        if not self._pending_username:
            return

        password = self._password_input.text()
        if verify_password(self._pending_username, password):
            username = self._pending_username
            self._reset_password_state()
            self.account_selected.emit(username)
        else:
            self._password_error.setText("Incorrect password. Try again.")
            self._password_input.clear()
            self._password_input.setFocus()

    def _cancel_password(self):
        self._reset_password_state()
        if self._grid_container is not None:
            self._grid_container.show()

    def _reset_password_state(self):
        self._pending_username = None
        self._password_input.clear()
        self._password_error.setText("")
        self._password_card.hide()

    def showEvent(self, event):
        # Refresh the tile list every time this page is shown, so a
        # freshly-added account actually appears without restarting the app.
        # Also make sure no stale password prompt carries over from a
        # previous visit to this page.
        self._reset_password_state()
        self._rebuild_grid()
        super().showEvent(event)