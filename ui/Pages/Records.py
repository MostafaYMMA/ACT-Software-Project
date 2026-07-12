"""
Records page: search across every timecard entry regardless of status
(Approved/Pending/Rejected), all in one place. Empty search shows
everything; typing filters live (debounced) via storage_service.search_records.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush

from ui.theme_utils import apply_live_style
from storage_service import search_records

SEARCH_DEBOUNCE_MS = 250

_STATUS_COLORS = {
    "Approved": "#4CAF50",
    "Pending": "#B58B00",
    "Rejected": "#f44336",
}

_COLUMNS = ["Status", "Subject", "Sender", "Project Number", "Project Name", "Task Name", "Date", "Qty"]


class RecordsPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(14)

        header_row = QHBoxLayout()
        title = QLabel("Records")
        apply_live_style(title, lambda c: f"font-size: 18px; font-weight: 700; color: {c['TEXT_PRIMARY']};")
        header_row.addWidget(title)
        header_row.addStretch()
        layout.addLayout(header_row)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search by subject, sender, project, task, or consultant...")
        self.search_input.textChanged.connect(self._on_search_text_changed)
        layout.addWidget(self.search_input)

        self.result_count_label = QLabel("")
        apply_live_style(self.result_count_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 11px;")
        layout.addWidget(self.result_count_label)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        apply_live_style(self.table, lambda c: f"""
            QTableWidget {{
                border: 1px solid {c['BORDER']}; background: {c['BG']}; color: {c['TEXT_PRIMARY']};
                gridline-color: {c['BORDER']};
            }}
            QTableWidget::item {{ color: {c['TEXT_PRIMARY']}; }}
            QHeaderView::section {{
                background-color: {c['SURFACE']}; color: {c['TEXT_PRIMARY']};
                padding: 6px; border: none; font-weight: 700;
            }}
        """)
        layout.addWidget(self.table, stretch=1)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._run_search)

        self._loaded_once = False

    def _on_search_text_changed(self, _text):
        self._debounce_timer.start(SEARCH_DEBOUNCE_MS)

    def _run_search(self):
        query = self.search_input.text()
        results = search_records(query)
        self._populate(results)

    def _populate(self, results):
        self.table.setRowCount(len(results))
        self.result_count_label.setText(f"{len(results)} record{'s' if len(results) != 1 else ''}")

        for row_index, record in enumerate(results):
            status = record.get("status", "")
            values = [
                status,
                record.get("subject") or "",
                record.get("sender") or "",
                record.get("Project Number") or "",
                record.get("Project Name") or "",
                record.get("Task Name") or "",
                record.get("Date") or "",
                record.get("Qty") or "",
            ]
            for col_index, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col_index == 0:
                    color = _STATUS_COLORS.get(status)
                    if color:
                        item.setForeground(Qt.GlobalColor.white)
                        item.setBackground(QBrush(QColor(color)))
                self.table.setItem(row_index, col_index, item)

    def showEvent(self, event):
        # Refresh every time the page is shown, so a new sync's results
        # actually appear without retyping the search.
        self._run_search()
        self._loaded_once = True
        super().showEvent(event)
