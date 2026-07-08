"""
Account creation page - username/password form.
Shown on first run (no accounts yet at all), or when the user chooses
"Add account" from the select-account screen.

Purely a view: it validates input and calls services.auth_service to
persist the account, then emits a signal - it doesn't know what happens
after that (main.py decides).
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton, QFrame
from PySide6.QtCore import Qt, Signal

from ui.athu import save_account
from ui.theme import COLOR_BG, COLOR_TEXT_PRIMARY, COLOR_TEXT_SECONDARY, COLOR_ERROR


class AccountCreationPage(QWidget):
    # Emitted with the new username once account creation succeeds.
    account_created = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {COLOR_BG};")
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setFixedWidth(340)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(10)

        title = QLabel("Create your account")
        title.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        card_layout.addWidget(title)

        subtitle = QLabel("This only needs to be done once per account.")
        subtitle.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px;")
        subtitle.setWordWrap(True)
        card_layout.addWidget(subtitle)
        card_layout.addSpacing(10)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Username")
        card_layout.addWidget(self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        card_layout.addWidget(self.password_input)

        self.confirm_input = QLineEdit()
        self.confirm_input.setPlaceholderText("Confirm password")
        self.confirm_input.setEchoMode(QLineEdit.EchoMode.Password)
        card_layout.addWidget(self.confirm_input)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color: {COLOR_ERROR}; font-size: 11px;")
        self.error_label.setWordWrap(True)
        card_layout.addWidget(self.error_label)

        create_btn = QPushButton("Create Account")
        create_btn.setObjectName("primaryButton")
        create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        create_btn.clicked.connect(self._handle_create)
        card_layout.addWidget(create_btn)

        outer.addWidget(card)

        for field in (self.username_input, self.password_input, self.confirm_input):
            field.returnPressed.connect(self._handle_create)

    def showEvent(self, event):
        # Clear the form each time this page is shown (e.g. re-entered
        # via "Add account" after a previous successful creation).
        self.username_input.clear()
        self.password_input.clear()
        self.confirm_input.clear()
        self.error_label.setText("")
        self.username_input.setFocus()
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