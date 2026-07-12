from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QThread, QObject

from ui.theme import COLOR_ACCENT, COLOR_TEXT_PRIMARY, COLOR_TEXT_SECONDARY, COLOR_BORDER
from sync_service import sync_cards
from storage_service import get_status_project_counts, get_status_rows


class StatCard(QFrame):
    clicked = Signal()

    def __init__(self, label, value, stripe_color):
        super().__init__()
        self._selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(84)
        self.setStyleSheet("background-color: #ffffff; border-radius: 0px;")
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

        if self._selected:
            self.setStyleSheet(
                "background-color: #FFF4E8; border: 1px solid #F0B36A; border-radius: 6px;"
            )
        else:
            self.setStyleSheet("background-color: #FBF3EC; border-radius: 6px;")

    def resizeEvent(self, event):
        self._apply_size()
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)

    def set_value(self, value):
        self.value_label.setText(str(value))


class SyncWorker(QObject):
    progress = Signal(str)
    finished = Signal()

    def run(self):
        sync_cards(progress_callback=self.progress.emit)
        self.finished.emit()


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

        self.scan_btn = QPushButton("Scan Inbox")
        self.scan_btn.setObjectName("primaryButton")
        self.scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scan_btn.clicked.connect(self.scan_inbox)
        header_row.addWidget(self.scan_btn)
        layout.addLayout(header_row)

        # Stat cards - placeholder values, replaced once scan logic is wired later
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        stat_defs = [
            ("approve", "Approved Mails", "#4CAF50"),
            ("pending", "Pending Mails", "#5F5F5F"),
            ("reject", "Rejected Mails", "#f44336"),
        ]
        self.stat_cards = {}
        for key, label, stripe_color in stat_defs:
            card = StatCard(label, "0", stripe_color)
            card.clicked.connect(lambda checked=False, card_key=key: self.select_stat_card(card_key))
            self.stat_cards[key] = card
            stats_row.addWidget(card)
        layout.addLayout(stats_row)

        # Matching subjects table
        # NOTE: only "Subject" for now - the scan only reads the subject
        # line, not the email body, so there's no consultant/project/period
        # data to show per row yet. That needs body-parsing logic first.
        self.table_title = QLabel("Matching subjects")
        self.table_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        layout.addWidget(self.table_title)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Subject", "Project Number", "Project Name", "Task Name", "Date", "Qty"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setStyleSheet(f"""
            QTableWidget {{ border: 1px solid {COLOR_BORDER}; background: white; color: #000000; }}
            QTableWidget::item {{ color: #000000; }}
            QHeaderView::section {{
                background-color: #FBF3EC; padding: 6px; border: none; font-weight: 700; color: #000000;
            }}
        """)
        layout.addWidget(self.table, stretch=1)

        self._set_empty_state()

    def select_stat_card(self, selected_key):
        for key, card in self.stat_cards.items():
            card.set_selected(key == selected_key)
        if hasattr(self, "table"):
            self._load_status_rows(selected_key)

    def _set_empty_state(self):
        for key in ("approve", "pending", "reject"):
            if key in self.stat_cards:
                self.stat_cards[key].set_value("—")
        self.table.setRowCount(0)
        self.table_title.setText("No data yet")

    def _refresh_stat_cards(self):
        counts = get_status_project_counts()
        for key in ("approve", "pending", "reject"):
            if key in self.stat_cards:
                self.stat_cards[key].set_value(counts.get(key, 0))

    def _load_status_rows(self, status_key):
        if not getattr(self, "_has_scanned", False):
            self.table.setRowCount(0)
            self.table_title.setText("No data yet")
            return

        status_labels = {
            "approve": "Approved",
            "pending": "Pending",
            "reject": "Rejected",
        }
        title = status_labels.get(status_key, "Approved")
        self.table.setRowCount(0)
        rows = get_status_rows(status_key)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.get("subject") or "",
                row.get("Project Number") or "",
                row.get("Project Name") or "",
                row.get("Task Name") or "",
                row.get("Date") or "",
                row.get("Qty") or "",
            ]
            for col_index, value in enumerate(values):
                self.table.setItem(row_index, col_index, QTableWidgetItem(str(value)))

        if hasattr(self, "table_title"):
            self.table_title.setText(f"{title} records")

    def scan_inbox(self):
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("Scanning...")
        self._has_scanned = False
        self._set_empty_state()

        self._sync_thread = QThread(self)
        self._sync_worker = SyncWorker()
        self._sync_worker.moveToThread(self._sync_thread)

        self._sync_thread.started.connect(self._sync_worker.run)
        self._sync_worker.progress.connect(self._on_sync_progress)
        self._sync_worker.finished.connect(self._on_sync_finished)
        self._sync_worker.finished.connect(self._sync_thread.quit)
        self._sync_thread.finished.connect(self._sync_thread.deleteLater)

        self._sync_thread.start()

    def _on_sync_progress(self, message):
        print(message)

    def _on_sync_finished(self):
        self._has_scanned = True
        self._has_scanned = True
        self._refresh_stat_cards()
        self.select_stat_card("approve")
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Scan Inbox")