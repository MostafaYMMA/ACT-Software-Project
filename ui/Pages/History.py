from datetime import date, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QDateEdit, QFileDialog, QMessageBox, QButtonGroup,
)
from PySide6.QtCore import Qt, QDate

from ui.theme_utils import apply_live_style
from storage_service import get_export_history, export_summary_csv_range, PROJECT_TYPE_LABELS

# Goes into the default filename of a division-only export, so the two
# divisions' files don't land on top of each other in the save dialog.
_FILENAME_SLUGS = {
    "beverage": "food_beverage",
    "hospitality": "hospitality",
}


def _last_month_range(today=None):
    """Returns (first_day, last_day) of the calendar month before today,
    as date objects."""
    today = today or date.today()
    first_of_this_month = today.replace(day=1)
    last_day_prev_month = first_of_this_month - timedelta(days=1)
    first_day_prev_month = last_day_prev_month.replace(day=1)
    return first_day_prev_month, last_day_prev_month


class HistoryPage(QWidget):
    """Export controls (last month / custom date range, both filtering
    by the date each email was RECEIVED - see storage_service._to_row)
    plus a log of every export ever produced (name + date), newest first."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(14)

        title = QLabel("Export History")
        apply_live_style(title, lambda c: f"font-size: 18px; font-weight: 700; color: {c['TEXT_PRIMARY']};")
        layout.addWidget(title)

        # --- Project type: which division an export covers ---
        # Same three choices, same rule and same None-means-all convention as
        # the Dashboard's toggle; here it narrows what goes INTO the file
        # rather than what's shown on screen.
        type_row = QHBoxLayout()
        type_row.setSpacing(8)

        type_label = QLabel("Project type:")
        apply_live_style(type_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 12px;")
        type_row.addWidget(type_label)

        self._selected_project_type = None
        self._project_type_group = QButtonGroup(self)
        self._project_type_group.setExclusive(True)

        type_defs = [(None, "All")] + list(PROJECT_TYPE_LABELS.items())
        for project_type, label in type_defs:
            button = QPushButton(label)
            button.setObjectName("periodToggle")
            button.setCheckable(True)
            button.setChecked(project_type is None)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.toggled.connect(
                lambda checked, value=project_type: self._on_project_type_changed(value) if checked else None
            )
            self._project_type_group.addButton(button)
            type_row.addWidget(button)

        type_row.addStretch()
        layout.addLayout(type_row)

        # --- Export controls ---
        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)

        last_month_btn = QPushButton("Export Last Month")
        last_month_btn.setObjectName("primaryButton")
        last_month_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        last_month_btn.clicked.connect(self._export_last_month)
        controls_row.addWidget(last_month_btn)

        controls_row.addSpacing(16)

        range_label = QLabel("or a date range:")
        apply_live_style(range_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 13px;")
        controls_row.addWidget(range_label)

        date_edit_style = lambda c: f"""
            QDateEdit {{
                border: 1px solid {c['BORDER']};
                border-radius: 6px;
                padding: 6px 8px;
                font-size: 13px;
                background: {c['SURFACE']};
                color: {c['TEXT_PRIMARY']};
            }}
            QDateEdit:focus {{ border: 1px solid {c['ACCENT']}; }}
            QDateEdit::drop-down {{ border: none; width: 18px; }}
        """

        self.from_date = QDateEdit()
        self.from_date.setCalendarPopup(True)
        self.from_date.setDisplayFormat("yyyy-MM-dd")
        self.from_date.setDate(QDate.currentDate().addMonths(-1))
        apply_live_style(self.from_date, date_edit_style)
        controls_row.addWidget(self.from_date)

        to_label = QLabel("to")
        apply_live_style(to_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 13px;")
        controls_row.addWidget(to_label)

        self.to_date = QDateEdit()
        self.to_date.setCalendarPopup(True)
        self.to_date.setDisplayFormat("yyyy-MM-dd")
        self.to_date.setDate(QDate.currentDate())
        apply_live_style(self.to_date, date_edit_style)
        controls_row.addWidget(self.to_date)

        range_btn = QPushButton("Export Range")
        range_btn.setObjectName("secondaryButton")
        range_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        range_btn.clicked.connect(self._export_range)
        controls_row.addWidget(range_btn)

        controls_row.addStretch()
        layout.addLayout(controls_row)

        self.status_label = QLabel("")
        apply_live_style(self.status_label, lambda c: f"font-size: 12px; color: {c['TEXT_SECONDARY']};")
        layout.addWidget(self.status_label)

        # --- Export history log ---
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Name", "Date"])
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

    # -----------------------------------------------------------------
    # Export actions
    # -----------------------------------------------------------------
    def _on_project_type_changed(self, project_type):
        self._selected_project_type = project_type

    def _default_name(self, start, end):
        """Filename offered in the save dialog. A division-only export says
        which division it is, so two exports of the same date range don't
        default to the same name."""
        slug = _FILENAME_SLUGS.get(self._selected_project_type)
        prefix = f"timecards_{slug}" if slug else "timecards"
        return f"{prefix}_{start}_to_{end}.csv"

    def _export_last_month(self):
        start, end = _last_month_range()
        start, end = start.isoformat(), end.isoformat()
        self._run_export(start, end, self._default_name(start, end))

    def _export_range(self):
        start = self.from_date.date().toPython().isoformat()
        end = self.to_date.date().toPython().isoformat()
        if start > end:
            QMessageBox.warning(self, "Invalid range", "The 'from' date must be before the 'to' date.")
            return
        self._run_export(start, end, self._default_name(start, end))

    def _run_export(self, start, end, default_name):
        path, _ = QFileDialog.getSaveFileName(self, "Save export as", default_name, "CSV files (*.csv)")
        if not path:
            return  # user cancelled

        project_type = self._selected_project_type
        row_count = export_summary_csv_range(start, end, path, project_type=project_type)

        # "no rows" means something different once a division is selected --
        # there may well be records in the range, just none of that division --
        # so say which it was rather than leaving the user to guess.
        scope = PROJECT_TYPE_LABELS.get(project_type)
        if row_count == 0:
            subject = f"No {scope} emails" if scope else "No emails"
            self.status_label.setText(f"{subject} received between {start} and {end} - nothing exported.")
        else:
            scope_text = f" {scope}" if scope else ""
            self.status_label.setText(
                f"Exported {row_count}{scope_text} row(s) (received {start} to {end}) to {path}"
            )

        self.refresh()

    # -----------------------------------------------------------------
    # Export history log
    # -----------------------------------------------------------------
    def refresh(self):
        rows = get_export_history()
        self.table.setRowCount(len(rows))
        for row_index, (name, date_str) in enumerate(rows):
            for col_index, value in enumerate((name, date_str)):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_index, col_index, item)

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)
