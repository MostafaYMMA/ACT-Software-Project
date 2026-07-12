from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
)


from ui.theme import COLOR_TEXT_PRIMARY, COLOR_BORDER
from storage_service import get_export_history


class HistoryPage(QWidget):
    """Lists every export ever produced (name + date), newest first."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(14)

        title = QLabel("Export History")

        title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        layout.addWidget(title)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Name", "Date"])
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

        self.refresh()

    def refresh(self):
        rows = get_export_history()
        self.table.setRowCount(len(rows))
        for row_index, (name, date) in enumerate(rows):
            self.table.setItem(row_index, 0, QTableWidgetItem(name))
            self.table.setItem(row_index, 1, QTableWidgetItem(date))

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)
