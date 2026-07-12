from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from ui.theme_manager import theme_manager
from ui.theme_utils import apply_live_style


class StatCard(QFrame):
    clicked = Signal()

    def __init__(self, label, value, stripe_color):
        super().__init__()
        self._selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(84)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        stripe = QFrame()
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(
            f"background-color: {stripe_color}; "
            f"border-top-left-radius: 6px; border-bottom-left-radius: 6px;"
        )
        layout.addWidget(stripe)

        inner = QVBoxLayout()
        inner.setContentsMargins(12, 10, 12, 10)
        inner.setSpacing(2)

        label_widget = QLabel(label)
        apply_live_style(label_widget, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 10px;")
        inner.addWidget(label_widget)

        value_widget = QLabel(value)
        apply_live_style(value_widget, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 22px; font-weight: 700;")
        inner.addWidget(value_widget)

        layout.addLayout(inner)
        self.value_label = value_widget  # exposed so callers can update it later

        # Selection styling depends on BOTH theme and selected-state, so it
        # can't use the simple apply_live_style helper (that only accounts
        # for theme). Re-run _apply_size on every theme change instead.
        theme_manager.theme_changed.connect(lambda _mode: self._apply_size())

    def set_selected(self, selected):
        self._selected = selected
        self._apply_size()

    def _apply_size(self):
        parent = self.parentWidget()
        if parent is None:
            return

        parent_width = max(1, parent.width())
        if self._selected:
            min_width = max(140, int(parent_width * 0.28))
            max_width = max(min_width, int(parent_width * 0.38))
        else:
            min_width = max(120, int(parent_width * 0.18))
            max_width = max(min_width, int(parent_width * 0.30))

        self.setMinimumWidth(min_width)
        self.setMaximumWidth(max_width)

        colors = theme_manager.colors()
        if self._selected:
            self.setStyleSheet(
                f"background-color: {colors['ACCENT_LIGHT']}; "
                f"border: 1px solid {colors['ACCENT']}; border-radius: 6px;"
            )
        else:
            self.setStyleSheet(f"background-color: {colors['SURFACE']}; border-radius: 6px;")

    def resizeEvent(self, event):
        self._apply_size()
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)

    def set_value(self, value):
        self.value_label.setText(str(value))


class DashboardPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(14)

        # Header row: page title + scan button
        header_row = QHBoxLayout()
        title = QLabel("Dashboard")
        apply_live_style(title, lambda c: f"font-size: 18px; font-weight: 700; color: {c['TEXT_PRIMARY']};")
        header_row.addWidget(title)
        header_row.addStretch()

        scan_btn = QPushButton("Scan Inbox")
        scan_btn.setObjectName("primaryButton")
        scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        scan_btn.clicked.connect(self.scan_inbox)  # TODO: wire real scan logic
        header_row.addWidget(scan_btn)
        layout.addLayout(header_row)

        # Stat cards - placeholder values, replaced once scan logic is wired later
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        stat_defs = [
            ("approve", "Approved Mails", "#4CAF50"),
            ("bending", "Bending Mails", "#5F5F5F"),
            ("reject", "Rejected Mails", "#f44336"),
        ]
        self.stat_cards = {}
        for key, label, stripe_color in stat_defs:
            card = StatCard(label, "0", stripe_color)
            card.clicked.connect(lambda checked=False, card_key=key: self.select_stat_card(card_key))
            self.stat_cards[key] = card
            stats_row.addWidget(card)
        self.select_stat_card("approve")
        layout.addLayout(stats_row)

        # Matching subjects table
        # NOTE: only "Subject" for now - the scan only reads the subject
        # line, not the email body, so there's no consultant/project/period
        # data to show per row yet. That needs body-parsing logic first.
        table_title = QLabel("Matching subjects")
        apply_live_style(table_title, lambda c: f"font-size: 13px; font-weight: 700; color: {c['TEXT_PRIMARY']};")
        layout.addWidget(table_title)

        self.table = QTableWidget(0, 1)
        self.table.setHorizontalHeaderLabels(["Subject"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        apply_live_style(self.table, lambda c: f"""
            QTableWidget {{
                border: 1px solid {c['BORDER']}; background: {c['BG']}; color: {c['TEXT_PRIMARY']};
                gridline-color: {c['BORDER']};
            }}
            QHeaderView::section {{
                background-color: {c['SURFACE']}; color: {c['TEXT_PRIMARY']};
                padding: 6px; border: none; font-weight: 700;
            }}
        """)
        layout.addWidget(self.table, stretch=1)

    def select_stat_card(self, selected_key):
        for key, card in self.stat_cards.items():
            card.set_selected(key == selected_key)

    def scan_inbox(self):
        # TODO: wire real Outlook scanning logic here later (will live in a
        # new services/outlook_service.py, kept separate from this UI file).
        print("Scan inbox clicked - no logic wired yet")