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
    QTableWidget, QTableWidgetItem, QCheckBox, QCompleter,
    QListView, QStyledItemDelegate,
)
from PySide6.QtCore import Qt, QTimer, QEvent, QSettings, QStringListModel, QRect
from PySide6.QtGui import QColor, QBrush, QPainter, QPen

from ui.theme_utils import apply_live_style
from ui.theme_manager import theme_manager
from ui.table_utils import order_columns, configure_grid, set_header_labels, fit_columns, HEADER_LABELS
from ui.profile_circle import SETTINGS_ORG, SETTINGS_APP
from ui.Pages.date_filter_header import FilterableHeaderView
from ui.Pages.date_range_popup import DateRangePopup
from storage_service import search_records, get_status_columns, SEARCHABLE_FIELDS

SEARCH_DEBOUNCE_MS = 250

# Recent-search dropdown (Chrome-style): what past queries typed into
# search_input are remembered, persisted the same way every other
# per-user setting in this app already is.
SEARCH_HISTORY_KEY = "records_search_history"
SEARCH_HISTORY_MAX = 15
# Width, in pixels, of the clickable "x" zone on the right of each
# history row - shared by the delegate that draws it and the click
# hit-test in RecordsPage.eventFilter.
_DELETE_ZONE_WIDTH = 24


class _HistoryItemDelegate(QStyledItemDelegate):
    """Draws a small trash-can icon on the right of every recent-search
    row so a single click can remove just that entry. Deliberately
    always-on rather than hover-only: QCompleter's popup grabs the
    mouse internally (to detect clicks outside closing it), which
    unreliably blocks hover/move events reaching this delegate - but
    plain clicks still land fine, so always-visible + click-to-delete
    is the version that actually works. The click handling itself
    lives in RecordsPage.eventFilter, which hit-tests the same
    _DELETE_ZONE_WIDTH region this paints."""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        delete_rect = QRect(
            option.rect.right() - _DELETE_ZONE_WIDTH, option.rect.top(),
            _DELETE_ZONE_WIDTH, option.rect.height(),
        )
        # White on dark mode, black on light mode, so it stays readable
        # against the popup's background either way - re-checked on
        # every paint, so it's always correct even if the mode was
        # toggled while this dropdown is open.
        icon_color = QColor(255, 255, 255) if theme_manager.mode == "dark" else QColor(20, 20, 20)

        # A faint circular backing, so this reads as an obvious
        # clickable button rather than a mark mixed in with the row's
        # own text.
        circle_diameter = min(delete_rect.height() - 8, 22)
        circle_rect = QRect(0, 0, circle_diameter, circle_diameter)
        circle_rect.moveCenter(delete_rect.center())
        backing = QColor(icon_color)
        backing.setAlpha(45)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(backing)
        painter.drawEllipse(circle_rect)

        # Hand-drawn trash-can icon - drawn with plain lines/rects
        # rather than a text glyph (e.g. "×"), since glyph rendering/
        # visibility for special characters turned out to be
        # unreliable inside this popup. This renders identically no
        # matter what font is active.
        pen = QPen(icon_color, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        cx, cy = delete_rect.center().x(), delete_rect.center().y()
        body = QRect(cx - 5, cy - 3, 10, 9)
        painter.drawRoundedRect(body, 1.5, 1.5)
        painter.drawLine(body.left() - 2, body.top() - 1, body.right() + 2, body.top() - 1)
        painter.drawRect(QRect(cx - 3, body.top() - 4, 6, 3))
        for rib_x in (cx - 2, cx, cx + 2):
            painter.drawLine(rib_x, body.top() + 2, rib_x, body.bottom() - 1)

        painter.restore()

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

        # Recent-search dropdown: same idea as Chrome's address bar -
        # shows past queries when the field is focused (even before
        # typing), then narrows to matches as you type. Picking one
        # fills the box and searches immediately.
        self._settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._history_model = QStringListModel(self._load_search_history())
        self._history_completer = QCompleter(self._history_model, self)
        self._history_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._history_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._history_view = QListView()
        # Click handling for the delete-x lands on the viewport, not the
        # QListView itself - mouse events over an item view's rows are
        # delivered there.
        self._history_view.viewport().installEventFilter(self)
        self._history_view.installEventFilter(self)  # for KeyPress (Delete/Backspace)
        # Without this, the popup renders with the OS's own default
        # colors instead of the app's theme - which is exactly why the
        # delete-x (colored to match theme_manager.mode) could go
        # invisible: it was assuming a background that wasn't actually
        # there. Styling the popup explicitly guarantees the two match.
        apply_live_style(self._history_view, lambda c: f"""
            QListView {{
                background-color: {c['SURFACE']}; color: {c['TEXT_PRIMARY']};
                border: 1px solid {c['BORDER']}; outline: none;
            }}
            QListView::item {{ padding: 6px 10px; }}
            QListView::item:selected, QListView::item:hover {{
                background-color: {c['ACCENT']}; color: white;
            }}
        """)
        self._history_completer.setPopup(self._history_view)
        # IMPORTANT: this must come AFTER setPopup(), not before.
        # QCompleter.setPopup() re-initializes the view it's given (sets
        # its own selection behavior, edit triggers, etc.) and was
        # silently discarding a delegate set beforehand - which is why
        # the delete-x never actually rendered in any earlier version,
        # regardless of its color or hover logic.
        self._history_view.setItemDelegate(_HistoryItemDelegate(self._history_view))
        self._history_completer.activated.connect(self._on_history_selected)
        theme_manager.theme_changed.connect(lambda _mode=None: self._history_view.viewport().update())
        self.search_input.setCompleter(self._history_completer)
        self.search_input.installEventFilter(self)
        self.search_input.returnPressed.connect(self._commit_search_to_history)

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
        # Custom header so the "Date" column gets a small clickable filter
        # arrow that opens a From/To calendar popup (date_range_popup.py).
        # Matched by column label (not index), so it stays on "Date" even
        # after a fresh search rebuilds the columns via set_header_labels.
        self._date_header = FilterableHeaderView({"Date"})
        self.table.setHorizontalHeader(self._date_header)
        self._date_header.filterIconClicked.connect(self._on_date_filter_icon_clicked)
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

        # Full, unfiltered-by-date result set from the last search, plus the
        # currently active Date filter (None, None means "no filter"). Kept
        # separate from what's on screen so applying/clearing the date
        # filter doesn't need to re-hit search_records.
        self._all_results = []
        self._date_range = (None, None)

        self._loaded_once = False

    def eventFilter(self, obj, event):
        # QCompleter only auto-pops up as you type by default. Chrome's
        # history dropdown also appears the moment the field is focused,
        # even with nothing typed yet - this reproduces that.
        if obj is self.search_input and event.type() == QEvent.Type.FocusIn:
            self._show_history_popup()
            return super().eventFilter(obj, event)

        if obj is self._history_view.viewport():
            if event.type() == QEvent.Type.MouseButtonPress:
                pos = event.position().toPoint()
                index = self._history_view.indexAt(pos)
                if index.isValid() and self._delete_rect_for(index).contains(pos):
                    self._delete_history_entry(index.data())
                    return True  # swallow the click, don't select/search it

        if obj is self._history_view:
            if event.type() == QEvent.Type.KeyPress and event.key() in (
                Qt.Key.Key_Delete, Qt.Key.Key_Backspace,
            ):
                index = self._history_view.currentIndex()
                if index.isValid():
                    self._delete_history_entry(index.data())
                    return True

        return super().eventFilter(obj, event)

    def _delete_rect_for(self, index):
        row_rect = self._history_view.visualRect(index)
        return QRect(
            row_rect.right() - _DELETE_ZONE_WIDTH, row_rect.top(),
            _DELETE_ZONE_WIDTH, row_rect.height(),
        )

    def _delete_history_entry(self, text):
        if not text:
            return
        history = self._settings.value(SEARCH_HISTORY_KEY, [], type=list)
        history = [entry for entry in history if entry != text]
        self._save_search_history(history)
        # Re-show rather than let the popup close, so removing a couple
        # of entries in a row doesn't require re-focusing the field
        # each time.
        self._show_history_popup()

    def _show_history_popup(self):
        if self._history_model.rowCount() == 0:
            return
        # Empty prefix matches every stored entry, so an empty field
        # shows the full recent-search list, same as Chrome.
        self._history_completer.setCompletionPrefix(self.search_input.text())
        self._history_completer.complete()

    def _load_search_history(self):
        return self._settings.value(SEARCH_HISTORY_KEY, [], type=list)

    def _save_search_history(self, history):
        self._settings.setValue(SEARCH_HISTORY_KEY, history)
        self._history_model.setStringList(history)

    def _commit_search_to_history(self):
        query = self.search_input.text().strip()
        if not query:
            return
        history = self._settings.value(SEARCH_HISTORY_KEY, [], type=list)
        # Case-insensitive de-dupe: searching the same thing again moves
        # it back to the top instead of appearing twice.
        history = [entry for entry in history if entry.lower() != query.lower()]
        history.insert(0, query)
        history = history[:SEARCH_HISTORY_MAX]
        self._save_search_history(history)

    def _on_history_selected(self, text):
        self.search_input.setText(text)
        self._commit_search_to_history()
        self._debounce_timer.stop()
        self._run_search()

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
        self._all_results = search_records(query, fields=self._selected_fields())
        self._apply_date_filter_and_populate()

    def _on_date_filter_icon_clicked(self, logical_index, anchor_pos):
        from_date, to_date = self._date_range
        popup = DateRangePopup(self, initial_from=from_date, initial_to=to_date)
        popup.rangeApplied.connect(self._on_date_range_applied)
        popup.cleared.connect(self._on_date_range_cleared)
        popup.move(anchor_pos)
        popup.show()

    def _on_date_range_applied(self, from_date, to_date):
        self._date_range = (from_date, to_date)
        self._date_header.set_filter_active(self._date_column_index(), bool(from_date or to_date))
        self._apply_date_filter_and_populate()

    def _on_date_range_cleared(self):
        self._date_range = (None, None)
        self._date_header.set_filter_active(self._date_column_index(), False)
        self._apply_date_filter_and_populate()

    def _date_column_index(self):
        columns = order_columns(list(self._all_results[0]) if self._all_results else ["status"] + get_status_columns())
        return columns.index("Date") if "Date" in columns else -1

    def _apply_date_filter_and_populate(self):
        from_date, to_date = self._date_range
        if not from_date and not to_date:
            self._populate(self._all_results)
            return
        from_str = from_date.toString("yyyy-MM-dd") if from_date else None
        to_str = to_date.toString("yyyy-MM-dd") if to_date else None
        filtered = []
        for record in self._all_results:
            value = record.get("Date")
            if not value:
                continue
            value_str = str(value)[:10]
            if from_str and value_str < from_str:
                continue
            if to_str and value_str > to_str:
                continue
            filtered.append(record)
        self._populate(filtered)

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