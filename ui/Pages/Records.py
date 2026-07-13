"""
Records page: search across every timecard entry regardless of status
(Approved/Pending/Rejected), all in one place. Empty search shows
everything; typing filters live (debounced) via storage_service.search_records.
The field checkboxes pick which columns the query matches against ("All"
keeps the search-everything behavior); the grid shows every column, like
the Dashboard's.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QCheckBox,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush

from ui.theme_utils import apply_live_style
from ui.table_utils import order_columns, configure_grid, set_header_labels, fit_columns, HEADER_LABELS
from storage_service import search_records, get_status_columns, SEARCHABLE_FIELDS

SEARCH_DEBOUNCE_MS = 250

_STATUS_COLORS = {
    "Approved": "#4CAF50",
    "Pending": "#B58B00",
    "Rejected": "#f44336",
}


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
        self.search_input.setPlaceholderText("Search records...")
        self.search_input.textChanged.connect(self._on_search_text_changed)
        layout.addWidget(self.search_input)

        # Field picker: "All" mirrors the old search-every-field behavior and
        # is the default; ticking specific fields narrows the match to just
        # those. The two are mutually exclusive -- picking a field unticks
        # All, ticking All clears the fields -- and unticking everything
        # falls back to All rather than leaving a search that matches nothing.
        fields_row = QHBoxLayout()
        fields_row.setSpacing(12)
        search_in_label = QLabel("Search in:")
        apply_live_style(search_in_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 11px;")
        fields_row.addWidget(search_in_label)

        self.all_fields_checkbox = QCheckBox("All")
        self.all_fields_checkbox.setChecked(True)
        self.all_fields_checkbox.toggled.connect(self._on_all_fields_toggled)
        apply_live_style(self.all_fields_checkbox, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 11px;")
        fields_row.addWidget(self.all_fields_checkbox)

        self.field_checkboxes = {}
        for field in SEARCHABLE_FIELDS:
            checkbox = QCheckBox(HEADER_LABELS.get(field, field))
            checkbox.toggled.connect(self._on_field_toggled)
            apply_live_style(checkbox, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 11px;")
            fields_row.addWidget(checkbox)
            self.field_checkboxes[field] = checkbox
        fields_row.addStretch()
        layout.addLayout(fields_row)

        self.result_count_label = QLabel("")
        apply_live_style(self.result_count_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 11px;")
        layout.addWidget(self.result_count_label)

        self.table = QTableWidget(0, 0)
        configure_grid(self.table)
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

    def _selected_fields(self):
        """Fields to search in, or None for all of them."""
        if self.all_fields_checkbox.isChecked():
            return None
        selected = [field for field, cb in self.field_checkboxes.items() if cb.isChecked()]
        return selected or None

    def _on_all_fields_toggled(self, checked):
        if checked:
            for checkbox in self.field_checkboxes.values():
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)
        elif not any(cb.isChecked() for cb in self.field_checkboxes.values()):
            # Unticking All directly with nothing else picked would leave no
            # field selected -- keep it on instead.
            self.all_fields_checkbox.blockSignals(True)
            self.all_fields_checkbox.setChecked(True)
            self.all_fields_checkbox.blockSignals(False)
            return
        self._debounce_timer.start(SEARCH_DEBOUNCE_MS)

    def _on_field_toggled(self, checked):
        any_field = any(cb.isChecked() for cb in self.field_checkboxes.values())
        self.all_fields_checkbox.blockSignals(True)
        self.all_fields_checkbox.setChecked(not any_field)
        self.all_fields_checkbox.blockSignals(False)
        self._debounce_timer.start(SEARCH_DEBOUNCE_MS)

    def _on_search_text_changed(self, _text):
        self._debounce_timer.start(SEARCH_DEBOUNCE_MS)

    def _run_search(self):
        query = self.search_input.text()
        results = search_records(query, fields=self._selected_fields())
        self._populate(results)

    def _populate(self, results):
        self.result_count_label.setText(f"{len(results)} record{'s' if len(results) != 1 else ''}")

        # No matches: keep the full headings up (status + the DB schema's
        # columns) rather than collapsing to an empty grid.
        columns = order_columns(list(results[0]) if results else ["status"] + get_status_columns())
        set_header_labels(self.table, columns)
        self.table.setRowCount(len(results))

        for row_index, record in enumerate(results):
            status = record.get("status", "")
            for col_index, column in enumerate(columns):
                value = record.get(column)
                item = QTableWidgetItem("" if value is None else str(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == "status":
                    color = _STATUS_COLORS.get(status)
                    if color:
                        item.setForeground(Qt.GlobalColor.white)
                        item.setBackground(QBrush(QColor(color)))
                self.table.setItem(row_index, col_index, item)

        fit_columns(self.table)

    def resizeEvent(self, event):
        # Re-fill on every resize, otherwise widening the window leaves a gap
        # on the right and narrowing it hides columns that would still fit.
        super().resizeEvent(event)
        fit_columns(self.table)

    def showEvent(self, event):
        # Refresh every time the page is shown, so a new sync's results
        # actually appear without retyping the search.
        self._run_search()
        self._loaded_once = True
        super().showEvent(event)
