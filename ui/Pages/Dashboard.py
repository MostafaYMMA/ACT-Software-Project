from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PySide6.QtCore import Qt

from ui.theme import COLOR_ACCENT, COLOR_TEXT_PRIMARY, COLOR_TEXT_SECONDARY, COLOR_BORDER


class StatCard(QFrame):
    def __init__(self, label, value, stripe_color):
        super().__init__()
        self.setStyleSheet("background-color: #FBF3EC; border-radius: 6px;")
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
        label_widget.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 10px;")
        inner.addWidget(label_widget)

        value_widget = QLabel(value)
        value_widget.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 22px; font-weight: 700;")
        inner.addWidget(value_widget)

        layout.addLayout(inner)
        self.value_label = value_widget  # exposed so callers can update it later

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
        title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
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
            ("total", "Total emails", COLOR_ACCENT),
            ("read", "Read emails", "#639922"),
            ("unread", "Unread emails", "#B4B2A9"),
            ("approved_count", "Approved subjects", "#0F6E56"),
            ("matching_count", "Approved + Time card/FW:FYI", "#D85A30"),
        ]
        self.stat_cards = {}
        for key, label, stripe_color in stat_defs:
            card = StatCard(label, "0", stripe_color)
            self.stat_cards[key] = card
            stats_row.addWidget(card)
        layout.addLayout(stats_row)

        # Matching subjects table
        # NOTE: only "Subject" for now - the scan only reads the subject
        # line, not the email body, so there's no consultant/project/period
        # data to show per row yet. That needs body-parsing logic first.
        table_title = QLabel("Matching subjects")
        table_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        layout.addWidget(table_title)

        self.table = QTableWidget(0, 1)
        self.table.setHorizontalHeaderLabels(["Subject"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setStyleSheet(f"""
            QTableWidget {{ border: 1px solid {COLOR_BORDER}; background: white; }}
            QHeaderView::section {{
                background-color: #FBF3EC; padding: 6px; border: none; font-weight: 700;
            }}
        """)
        layout.addWidget(self.table, stretch=1)

    def scan_inbox(self):
        # TODO: wire real Outlook scanning logic here later (will live in a
        # new services/outlook_service.py, kept separate from this UI file).
        print("Scan inbox clicked - no logic wired yet")