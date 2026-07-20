"""
Current Sheet: the one editable view of the current (not-yet-finalized)
period's data -- every status (Approved/Pending/Rejected) together in a
single table, no status filter. This is the only place editing happens;
Dashboard's table is a live overview, not an editing surface.

Rows are keyed by (status, id) rather than just id, since the same id can
exist in more than one status table -- every write here routes back
through storage_service.update_status_record_field(status, id, ...).

The "Update" button just re-reads from the local database -- it does not
touch Outlook (that's Dashboard's Scan Inbox) and does not talk to any
other device or shared location. Once a shared (OneDrive-hosted) database
exists, this is the button that will pull from it; for now it's a plain
local refresh.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QButtonGroup, QMenu,
    QStyledItemDelegate, QStyleOptionViewItem, QStyle,
)
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QColor, QPixmap, QIcon, QPainter, QPen

from ui.theme_manager import theme_manager

from ui.theme_utils import apply_live_style
from ui.loading_overlay import LoadingOverlay
from ui.project_type_settings import project_type_settings
from ui.table_utils import order_columns, configure_grid, fit_columns, HEADER_LABELS
from storage_service import (
    get_current_sheet_rows, update_status_record_field, PROJECT_TYPE_LABELS,
)

# Column keys that only make sense as numbers -- a non-numeric entry is
# rejected and the cell reverts, same rule Dashboard's editor uses.
_NUMERIC_COLUMNS = {"rate", "Qty"}

_STATUS_COLORS = {
    "Approved": "#4CAF50",
    "Pending": "#B58B00",
    "Rejected": "#f44336",
}

# Small preset palette for row highlighting -- deliberately a short fixed
# list (not a full color picker): this is a quick visual tag, not a
# design tool, so a handful of clearly-distinct choices plus "None" is
# more useful than an overwhelming picker.
_HIGHLIGHT_PALETTE = [
    ("none", None, "None"),
    ("red", "#F44336", "Red"),
    ("orange", "#FF9800", "Orange"),
    ("yellow", "#FDD835", "Yellow"),
    ("green", "#4CAF50", "Green"),
    ("blue", "#42A5F5", "Blue"),
]
# Row background tint is a light wash of the swatch color, not the solid
# color itself -- a solid fill would fight with the row's own text/
# selection contrast; a light wash reads as "tagged" without hurting
# legibility.
_HIGHLIGHT_ROW_TINT_ALPHA = 90


def _swatch_icon(color_hex):
    """A small solid-color circle icon for one palette entry (or a plain
    ring for 'None')."""
    size = 16
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if color_hex:
        painter.setBrush(QColor(color_hex))
        painter.setPen(Qt.PenStyle.NoPen)
    else:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#888888"), 1.5))
    painter.drawEllipse(2, 2, size - 4, size - 4)
    painter.end()
    return QIcon(pixmap)


class _HighlightButton(QPushButton):
    """One row's highlight swatch, shown via setCellWidget. A plain
    QTableWidgetItem can't host a click-to-open-a-menu control, so the
    highlight column uses a real button widget instead."""

    def __init__(self, row_status, row_id, current_color, on_pick):
        super().__init__()
        self.row_status = row_status
        self.row_id = row_id
        self.on_pick = on_pick
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIcon(_swatch_icon(current_color))
        self.setIconSize(QSize(16, 16))
        self.setFlat(True)
        self.setStyleSheet("QPushButton { border: none; background: transparent; }")
        self.clicked.connect(self._open_menu)

    def _open_menu(self):
        menu = QMenu(self)
        for key, color_hex, label in _HIGHLIGHT_PALETTE:
            action = menu.addAction(_swatch_icon(color_hex), label)
            action.triggered.connect(
                lambda checked=False, c=color_hex: self.on_pick(self.row_status, self.row_id, c)
            )
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))


class _HighlightAwareDelegate(QStyledItemDelegate):
    """Paints table cells entirely by hand instead of delegating to the
    base class's usual stylesheet-driven painting.

    ui/theme.py sets a GLOBAL, app-wide "QTableWidget::item { ... }" rule
    (applied once via QApplication.setStyleSheet() in main.py) that every
    table in the app inherits, including this one -- and that's enough to
    trigger a well-known Qt quirk where stylesheet-driven item painting
    silently ignores Qt::BackgroundRole. item.setBackground() "succeeds"
    (the data is genuinely stored) but the built-in paint path never
    draws it. Since that global rule also supplies real, needed styling
    (padding, selection color, etc.) for every OTHER table in the app, it
    can't just be removed -- so instead this delegate bypasses the
    stylesheet's item-painting step entirely, but only for THIS table
    (installed via setItemDelegate below), leaving every other page's
    tables untouched.
    """

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        painter.save()

        if opt.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(opt.rect, opt.palette.highlight())
            text_color = opt.palette.highlightedText().color()
        else:
            background = index.data(Qt.ItemDataRole.BackgroundRole)
            if background is not None:
                painter.fillRect(opt.rect, background)
            foreground = index.data(Qt.ItemDataRole.ForegroundRole)
            if foreground is not None:
                text_color = foreground.color() if hasattr(foreground, "color") else QColor(foreground)
            else:
                text_color = opt.palette.text().color()

        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text:
            painter.setPen(text_color)
            painter.setFont(opt.font)
            alignment = index.data(Qt.ItemDataRole.TextAlignmentRole)
            if not alignment:
                alignment = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            padded_rect = opt.rect.adjusted(6, 0, -6, 0)
            painter.drawText(padded_rect, int(alignment), str(text))

        painter.restore()


class CurrentSheetPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(14)

        header_row = QHBoxLayout()
        title = QLabel("Current Sheet")
        apply_live_style(title, lambda c: f"font-size: 18px; font-weight: 700; color: {c['TEXT_PRIMARY']};")
        header_row.addWidget(title)
        header_row.addStretch()

        # "Update": a plain local re-read from the database, not an
        # Outlook scan (that's Dashboard) and not (yet) a pull from any
        # shared/online location -- see module docstring.
        self.update_btn = QPushButton("Update")
        self.update_btn.setObjectName("secondaryButton")
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.clicked.connect(self.refresh)
        header_row.addWidget(self.update_btn)
        layout.addLayout(header_row)

        subtitle = QLabel(
            "Everything received since the last finalize, all statuses together. "
            "Double-click a cell to edit; click the dot to highlight a row."
        )
        apply_live_style(subtitle, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 12px;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # Project-type row: same synced filter as Dashboard/History (see
        # ui/project_type_settings.py) -- narrows which division's rows
        # show here, independent of the "no status filter" rule, which is
        # a different axis entirely.
        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        type_label = QLabel("Project type:")
        apply_live_style(type_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 12px;")
        type_row.addWidget(type_label)

        type_defs = [(None, "All")] + list(PROJECT_TYPE_LABELS.items())
        self._project_type_group = QButtonGroup(self)
        self._project_type_group.setExclusive(True)
        self._project_type_buttons = {}
        self._selected_project_type = project_type_settings.project_type

        for project_type, label in type_defs:
            button = QPushButton(label)
            button.setObjectName("periodToggle")
            button.setCheckable(True)
            button.setChecked(project_type == project_type_settings.project_type)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.toggled.connect(
                lambda checked, value=project_type: self._on_project_type_toggled(value) if checked else None
            )
            self._project_type_group.addButton(button)
            self._project_type_buttons[project_type] = button
            type_row.addWidget(button)

        project_type_settings.project_type_changed.connect(self._sync_project_type_selection)
        type_row.addStretch()
        layout.addLayout(type_row)

        # --- The sheet itself ---
        table_container = QWidget()
        table_container_layout = QVBoxLayout(table_container)
        table_container_layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 0)
        configure_grid(self.table)
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._highlight_delegate = _HighlightAwareDelegate(self.table)
        self.table.setItemDelegate(self._highlight_delegate)
        self._populating_table = False
        self._displayed_columns = []
        self._displayed_rows = []
        self.table.itemChanged.connect(self._on_item_changed)
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
        # Re-populates on a theme switch, now that item text color is set
        # explicitly in code rather than via the QTableWidget::item QSS
        # rule removed above (that rule, though it only set `color`, was
        # enough to trigger a well-known Qt quirk where stylesheet-driven
        # item painting silently ignores Qt::BackgroundRole -- i.e.
        # item.setBackground() "succeeds" but is never actually drawn,
        # which is why highlighted rows never showed a tint).
        theme_manager.theme_changed.connect(lambda _mode: self.refresh())
        table_container_layout.addWidget(self.table)
        layout.addWidget(table_container, stretch=1)

        self._loading_overlay = LoadingOverlay(table_container, message="Loading sheet...")
        table_container.resizeEvent = lambda event: self._on_table_container_resized()

        self.refresh()

    def _on_table_container_resized(self):
        self._loading_overlay.reposition()
        self._fit_columns()

    def _fit_columns(self):
        fit_columns(self.table)

    def _on_project_type_toggled(self, project_type):
        project_type_settings.set_project_type(project_type)

    def _sync_project_type_selection(self, project_type):
        button = self._project_type_buttons.get(project_type)
        if button is not None:
            button.setChecked(True)
        self._selected_project_type = project_type
        self.refresh()

    def refresh(self):
        self._loading_overlay.start("Loading sheet...")
        rows = get_current_sheet_rows(project_type=self._selected_project_type)
        self._populate(rows)
        self._loading_overlay.stop()

    def _columns_for(self, rows):
        if not rows:
            return order_columns(["status", "subject", "Project Number", "Project Name",
                                   "Task Name", "Date", "Qty", "rate"])
        # Every row shares the same shape (all three status tables have
        # identical columns), so the first row's keys are enough.
        return order_columns([key for key in rows[0].keys() if key != "highlight_color"])

    def _populate(self, rows):
        columns = self._columns_for(rows)
        self._displayed_columns = columns
        self._displayed_rows = rows
        text_color = QColor(theme_manager.colors()["TEXT_PRIMARY"])

        self._populating_table = True
        try:
            # +1 for the highlight column, always first.
            self.table.setColumnCount(len(columns) + 1)
            self.table.setHorizontalHeaderLabels(
                ["\u2022"] + [self._header_label(c) for c in columns]
            )
            self.table.setRowCount(len(rows))

            for row_index, row in enumerate(rows):
                highlight_btn = _HighlightButton(
                    row.get("status"), row.get("id"), row.get("highlight_color"), self._on_highlight_picked
                )
                self.table.setCellWidget(row_index, 0, highlight_btn)

                tint = self._row_tint(row.get("highlight_color"))
                for col_index, column in enumerate(columns, start=1):
                    value = row.get(column)
                    if column == "status":
                        item = QTableWidgetItem(value or "")
                        item.setForeground(QColor(_STATUS_COLORS.get(value, "#888888")))
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    else:
                        item = QTableWidgetItem("" if value is None else str(value))
                        item.setForeground(text_color)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if tint is not None:
                        item.setBackground(tint)
                    self.table.setItem(row_index, col_index, item)
        finally:
            self._populating_table = False

        self._fit_columns()

    def _header_label(self, column):
        return HEADER_LABELS.get(column, column)

    def _row_tint(self, color_hex):
        if not color_hex:
            return None
        color = QColor(color_hex)
        color.setAlpha(_HIGHLIGHT_ROW_TINT_ALPHA)
        return color

    def _on_highlight_picked(self, status, record_id, color_hex):
        if update_status_record_field(status, record_id, "highlight_color", color_hex):
            # NOT a direct self.refresh() call: this runs inside a QMenu
            # action's triggered handler, which is itself still on the
            # call stack underneath the button that opened it (see
            # _HighlightButton._open_menu -- menu.exec() is blocking).
            # refresh() rebuilds the whole table and replaces every cell
            # widget, including the very button whose click is still
            # being handled -- deleting it out from under its own
            # in-progress event handler would corrupt the table (this is
            # what caused the whole sheet to appear to vanish). Queuing
            # the refresh for the next event loop tick lets that call
            # stack finish unwinding first.
            QTimer.singleShot(0, self.refresh)

    def _on_item_changed(self, item):
        if self._populating_table:
            return

        row_index, col_index = item.row(), item.column()
        if col_index == 0:
            return  # the highlight column is a cell widget, not an editable item
        column_index = col_index - 1
        if row_index >= len(self._displayed_rows) or column_index >= len(self._displayed_columns):
            return

        record = self._displayed_rows[row_index]
        column = self._displayed_columns[column_index]
        if column == "status":
            return  # status is display-only here; changing it belongs on Dashboard's stat cards, not a text edit

        old_value = record.get(column)
        new_value = item.text().strip()

        def revert():
            self._populating_table = True
            try:
                item.setText("" if old_value is None else str(old_value))
            finally:
                self._populating_table = False

        if str(old_value or "") == new_value:
            return

        if column in _NUMERIC_COLUMNS:
            try:
                new_value = float(new_value) if new_value else 0.0
            except ValueError:
                revert()
                return

        status = record.get("status")
        record_id = record.get("id")
        if update_status_record_field(status, record_id, column, new_value):
            record[column] = new_value
        else:
            revert()