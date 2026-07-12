"""
Late tab: a brief of every Pending/Rejected record that's been sitting
for at least the threshold configured in Settings (see
ui/notification_settings.py), each row saying how long it's been stuck.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush

from ui.theme_utils import apply_live_style
from ui.notification_settings import notification_settings
from storage_service import get_stale_records

_STATUS_COLORS = {
    "Pending": "#B58B00",
    "Rejected": "#f44336",
}

_COLUMNS = ["Status", "Subject", "Project Number", "Project Name", "Task Name", "Date", "Been like this for"]


def _format_age(hours):
    if hours < 24:
        h = int(hours)
        return f"{h} hour{'s' if h != 1 else ''}"
    days = hours / 24
    if days < 7:
        d = int(days)
        return f"{d} day{'s' if d != 1 else ''}"
    weeks = int(days // 7)
    return f"{weeks} week{'s' if weeks != 1 else ''}"


def _format_threshold(hours):
    if hours >= 24 and hours % 24 == 0:
        days = int(hours // 24)
        return f"{days} day{'s' if days != 1 else ''}"
    return f"{int(hours)} hour{'s' if int(hours) != 1 else ''}"


class LatePage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(14)

        title = QLabel("Late")
        apply_live_style(title, lambda c: f"font-size: 18px; font-weight: 700; color: {c['TEXT_PRIMARY']};")
        layout.addWidget(title)

        self.subtitle = QLabel("")
        apply_live_style(self.subtitle, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 11px;")
        layout.addWidget(self.subtitle)

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

        self.refresh()

    def refresh(self):
        threshold_hours = notification_settings.threshold_hours
        records = get_stale_records(threshold_hours)

        self.subtitle.setText(
            f"Pending or rejected for at least {_format_threshold(threshold_hours)} "
            f"({len(records)} record{'s' if len(records) != 1 else ''}) -- "
            f"change the threshold in Settings."
        )

        self.table.setRowCount(len(records))
        for row_index, record in enumerate(records):
            status = record.get("status", "")
            values = [
                status,
                record.get("subject") or "",
                record.get("Project Number") or "",
                record.get("Project Name") or "",
                record.get("Task Name") or "",
                record.get("Date") or "",
                _format_age(record.get("age_hours", 0)),
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
        self.refresh()
        super().showEvent(event)
