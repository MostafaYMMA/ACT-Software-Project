"""
Account creation page - username/password form.
Shown on first run (no accounts yet at all), or when the user chooses
"Add account" from the select-account screen.

Purely a view: it validates input and calls services.auth_service to
persist the account, then emits a signal - it doesn't know what happens
after that (main.py decides).

Theming follows the live-theme pattern used everywhere else (see
ui/select_account_page.py / ui/theme_utils.py): apply_live_style()
re-applies styling immediately AND every time theme_manager.theme_changed
fires, so this page recolors live in both light and dark mode instead of
being stuck on hardcoded light colors.

A "Back" button is shown at the top-left of the card, but ONLY when at
least one account already exists on this machine - i.e. when this page
was reached via "Add account" from the select-account screen. On a true
first run (no accounts at all) there is nowhere sensible to go back to,
so the button stays hidden. Visibility is (re)checked every time the
page is shown, since account_page is a long-lived singleton reused for
both entry paths.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFrame,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette, QColor

from ui.athu import save_account, accounts_exist
from ui.password_field import PasswordField
from ui.theme_manager import theme_manager
from ui.theme_utils import apply_live_style


def _username_input_style(colors) -> str:
    return f"""
        QLineEdit {{
            background-color: {colors['BG']}; color: {colors['TEXT_PRIMARY']};
            border: 1px solid {colors['BORDER']}; border-radius: 6px;
            padding: 8px;
        }}
        QLineEdit:focus {{ border: 1px solid {colors['ACCENT']}; }}
    """


class AccountCreationPage(QWidget):
    # Emitted with the new username once account creation succeeds.
    account_created = Signal(str)
    # Emitted when the user taps "Back" (only shown/reachable when they
    # got here via "Add account", so main.py can route back to the
    # select-account screen).
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Plain QWidgets don't paint a CSS background-color unless this
        # attribute is set - without it, only child widgets show color,
        # never the page itself.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        apply_live_style(self, lambda c: f"background-color: {c['BG']};")
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setFixedWidth(340)
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        apply_live_style(card, lambda c: f"""
            QFrame {{
                background-color: {c['SURFACE']};
                border: 1px solid {c['BORDER']};
                border-radius: 10px;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(10)

        # -- back row (button only, hidden unless other accounts exist) --
        back_row = QHBoxLayout()
        back_row.setContentsMargins(0, 0, 0, 0)
        self.back_btn = QPushButton("\u2190 Back")
        self.back_btn.setObjectName("accountBackBtn")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.clicked.connect(self.back_requested.emit)
        apply_live_style(self.back_btn, lambda c: f"""
            QPushButton#accountBackBtn {{
                background-color: transparent;
                color: {c['TEXT_PRIMARY']};
                border: 1px solid {c['ACCENT']};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#accountBackBtn:hover {{
                background-color: {c['ACCENT_LIGHT']};
            }}
        """)
        back_row.addWidget(self.back_btn)
        back_row.addStretch(1)
        card_layout.addLayout(back_row)

        title = QLabel("Create your account")
        apply_live_style(title, lambda c: (
            f"font-size: 20px; font-weight: 700; color: {c['TEXT_PRIMARY']}; "
            f"background-color: transparent;"
        ))
        card_layout.addWidget(title)

        subtitle = QLabel("This only needs to be done once per account.")
        apply_live_style(subtitle, lambda c: (
            f"color: {c['TEXT_SECONDARY']}; font-size: 12px; background-color: transparent;"
        ))
        subtitle.setWordWrap(True)
        card_layout.addWidget(subtitle)
        card_layout.addSpacing(10)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Username")
        self._style_line_edit(self.username_input)
        card_layout.addWidget(self.username_input)

        self.password_input = PasswordField(placeholder="Password")
        card_layout.addWidget(self.password_input)

        self.confirm_input = PasswordField(placeholder="Confirm password")
        card_layout.addWidget(self.confirm_input)

        self.error_label = QLabel("")
        apply_live_style(self.error_label, lambda c: (
            f"color: {c['ERROR']}; font-size: 11px; background-color: transparent;"
        ))
        self.error_label.setWordWrap(True)
        card_layout.addWidget(self.error_label)

        create_btn = QPushButton("Create Account")
        create_btn.setObjectName("primaryButton")
        create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        create_btn.clicked.connect(self._handle_create)
        card_layout.addWidget(create_btn)

        outer.addWidget(card)

        self.username_input.returnPressed.connect(self._handle_create)
        self.password_input.returnPressed.connect(self._handle_create)
        self.confirm_input.returnPressed.connect(self._handle_create)

    def _style_line_edit(self, line_edit):
        """Applies theme-aware colors AND a theme-aware placeholder-text
        color. Placeholder color isn't reliably settable through QSS
        alone, so it's set via QPalette here - the low-contrast/near-
        invisible placeholder text was the main "text isn't visible"
        issue on this page."""
        def _apply(_mode=None):
            colors = theme_manager.colors()
            line_edit.setStyleSheet(_username_input_style(colors))
            palette = line_edit.palette()
            palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["TEXT_SECONDARY"]))
            palette.setColor(QPalette.ColorRole.Text, QColor(colors["TEXT_PRIMARY"]))
            line_edit.setPalette(palette)
        _apply()
        theme_manager.theme_changed.connect(_apply)

    def showEvent(self, event):
        # Clear the form each time this page is shown (e.g. re-entered
        # via "Add account" after a previous successful creation).
        self.username_input.clear()
        self.password_input.clear()
        self.confirm_input.clear()
        self.error_label.setText("")
        self.username_input.setFocus()
        # Only offer a way back if there's somewhere to go back to.
        self.back_btn.setVisible(accounts_exist())
        super().showEvent(event)

    def _handle_create(self):
        username = self.username_input.text().strip()
        password = self.password_input.text()
        confirm = self.confirm_input.text()

        if not username or not password or not confirm:
            self.error_label.setText("Please fill in all fields.")
            return
        if password != confirm:
            self.error_label.setText("Passwords do not match.")
            return
        if len(password) < 4:
            self.error_label.setText("Password must be at least 4 characters.")
            return

        try:
            save_account(username, password)
        except ValueError as e:
            self.error_label.setText(str(e))
            return

        self.account_created.emit(username)