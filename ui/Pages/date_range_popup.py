"""
Small popup calendar for picking a From/To date range, opened from
FilterableHeaderView's filter arrow. Deliberately knows nothing about
QTableWidget or the storage layer -- it just hands back
(from_date, to_date) as QDate (or None) via signals, so it can be wired
up to filter any table's rows without this widget needing to change.
"""

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QCalendarWidget, QHBoxLayout, QPushButton, QVBoxLayout, QWidget,
)

from ui.theme_utils import apply_live_style


class DateRangePopup(QWidget):
    rangeApplied = Signal(object, object)  # QDate|None, QDate|None
    cleared = Signal()

    def __init__(self, parent=None, initial_from=None, initial_to=None):
        # Qt.Popup: closes automatically on an outside click, same
        # behavior as the QCompleter dropdown already used in Records.py.
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._from_date = initial_from
        self._to_date = initial_to
        self._picking = "from"  # which end of the range the calendar edits next

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        endpoints_row = QHBoxLayout()
        self.from_button = QPushButton()
        self.to_button = QPushButton()
        for button, key in ((self.from_button, "from"), (self.to_button, "to")):
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, k=key: self._set_picking(k))
            endpoints_row.addWidget(button)
        layout.addLayout(endpoints_row)

        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.clicked.connect(self._on_date_picked)
        layout.addWidget(self.calendar)

        buttons_row = QHBoxLayout()
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self._on_clear)
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self._on_apply)
        buttons_row.addWidget(clear_button)
        buttons_row.addStretch()
        buttons_row.addWidget(apply_button)
        layout.addLayout(buttons_row)

        apply_live_style(self, lambda c: f"""
            QWidget {{ background-color: {c['SURFACE']}; color: {c['TEXT_PRIMARY']}; border: 1px solid {c['BORDER']}; }}
            QPushButton {{ background-color: {c['BG']}; color: {c['TEXT_PRIMARY']}; border: 1px solid {c['BORDER']}; padding: 5px 10px; border-radius: 4px; }}
            QPushButton:checked {{ background-color: #FF7A00; color: white; border-color: #FF7A00; }}
        """)

        self._set_picking("from")
        self._refresh_endpoint_labels()
        if self._from_date:
            self.calendar.setSelectedDate(self._from_date)

    def _set_picking(self, which):
        self._picking = which
        self.from_button.setChecked(which == "from")
        self.to_button.setChecked(which == "to")

    def _refresh_endpoint_labels(self):
        self.from_button.setText(f"From: {self._from_date.toString('yyyy-MM-dd') if self._from_date else '—'}")
        self.to_button.setText(f"To: {self._to_date.toString('yyyy-MM-dd') if self._to_date else '—'}")

    def _on_date_picked(self, date: QDate):
        if self._picking == "from":
            self._from_date = date
            self._set_picking("to")  # move on to picking the end date next
        else:
            self._to_date = date
        # Keep the range sane if the user picks the two dates out of order.
        if self._from_date and self._to_date and self._from_date > self._to_date:
            self._from_date, self._to_date = self._to_date, self._from_date
        self._refresh_endpoint_labels()

    def _on_clear(self):
        self.cleared.emit()
        self.close()

    def _on_apply(self):
        self.rangeApplied.emit(self._from_date, self._to_date)
        self.close()