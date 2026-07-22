"""
The Current Sheet: a working view of the approved timecards that sits
between the raw scan results and the final export.

Everything on it is manual. Cells are edited by hand (double-click, same as
the Dashboard grid) and rows are coloured by hand (right-click). Neither is
driven by anything the app works out for itself: no status, division, age
or export state sets a colour, and nothing reads one back. A colour means
whatever the person who set it decided it means.

Rows appear here by being scanned and approved -- there is no way to add a
row that didn't come from a real approved timecard email (see
storage_service.sync_current_sheet). Edits and colours survive every later
scan untouched.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QMenu, QToolButton, QMessageBox, QStyle, QStyledItemDelegate,
)
from PySide6.QtCore import Qt, QThread, QSettings, QTimer, QDate, QEvent
from PySide6.QtGui import QColor, QIcon, QPixmap, QFontMetrics

from ui.theme_manager import theme_manager
from ui.theme_utils import apply_live_style
from ui.project_type_settings import project_type_settings
from ui.sync_partner_settings import sync_partner_settings
from ui.sync_workers import (
    UpdateWorker, LocalUpdateWorker, FinalizeWorker, LocalFinalizeWorker,
)
from ui.profile_circle import SETTINGS_ORG, SETTINGS_APP
from ui.table_utils import order_columns, configure_grid, fit_columns, HEADER_LABELS
from ui.transition import reveal_rows
from storage_service import (
    get_current_sheet_rows, update_current_sheet_field, set_current_sheet_row_color,
    get_active_export_path, get_last_export_date,
)

# Same key ui/Pages/Settings.py's Sync switch writes to, read the same way
# ui/Pages/History.py reads it -- the Update button below is the same
# button, so it has to make the same sync-on/sync-off choice.
SYNC_ENABLED_KEY = "sync_enabled"

# Columns the user shouldn't retype: "Date" and "received" are what the
# scan actually saw, and rewriting them here would put the row out of step
# with the record it came from without correcting anything.
_READ_ONLY_COLUMNS = {"received"}

# Cell edits that have to parse as a number before they're worth saving --
# same two the Dashboard grid guards (see Dashboard._NUMERIC_COLUMNS).
_NUMERIC_COLUMNS = {"rate", "Qty"}

# Per-cell delay of the colour ripple. Deliberately short -- this is a
# "which row did I just mark" cue, not a spectacle, and the row has to be
# fully coloured before the user's eye has moved on.
_RIPPLE_STEP_MS = 22

# The same six swatches the SharePoint View Current window offers (see
# ui/Pages/History.py's _HIGHLIGHT_PALETTE) -- one highlight vocabulary
# across the app rather than two that drift apart.
HIGHLIGHT_PALETTE = [
    ("None", None),
    ("Red", "#E05252"),
    ("Orange", "#FF7A00"),
    ("Yellow", "#FFEE33"),
    ("Green", "#4CAF50"),
    ("Blue", "#4A90D9"),
    ("Purple", "#9B59B6"),
]

# The colour-picker button lives in its own leading column, so the data
# columns all sit one to the right of where the record dict has them.
_COLOR_COLUMN = 0
_DATA_COLUMN_OFFSET = 1

# The swatch is a circle of this diameter, and its column is pinned just
# wide enough to hold it -- fit_columns sizes columns to their contents,
# which for an empty header would leave a wide, obviously-empty gap.
_SWATCH_DIAMETER = 14
_COLOR_COLUMN_WIDTH = 34

# How far a tinted row is pulled toward the theme's selection colour when
# it's selected, and how much hover darkens it. Both deliberately small:
# the point is that you can see BOTH the highlight you chose and the fact
# that the row is selected/hovered, rather than one replacing the other.
_SELECTED_BLEND = 0.35
_HOVER_DARKEN = 112  # QColor.darker() percentage


def _blend(base, other, factor):
    """base moved `factor` of the way toward `other`."""
    return QColor(
        round(base.red() * (1 - factor) + other.red() * factor),
        round(base.green() * (1 - factor) + other.green() * factor),
        round(base.blue() * (1 - factor) + other.blue() * factor),
    )


def _relative_luminance(color):
    """WCAG 2.x relative luminance (sRGB, gamma-corrected). Not the naive
    weighted average of the raw bytes -- that overstates how light a
    saturated colour is and picks unreadable text for it."""
    def channel(value):
        value = value / 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(color.red())
        + 0.7152 * channel(color.green())
        + 0.0722 * channel(color.blue())
    )


def _contrast_ratio(first, second):
    """WCAG contrast ratio between two relative luminances (1:1 to 21:1)."""
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def readable_text_color(color):
    """Black or white -- whichever has the higher WCAG contrast against
    `color`.

    A tint is user-chosen from a palette spanning #FFEE33 (very light) to
    #9B59B6 (mid-dark), and the theme's own text colour is fixed, so it
    would vanish against roughly half of them. Picking per-colour by
    measured contrast is what keeps every combination legible; a single
    reduced-opacity tint could not, in both light and dark themes."""
    background = _relative_luminance(color)
    return (
        "#000000" if _contrast_ratio(background, 0.0) >= _contrast_ratio(1.0, background)
        else "#FFFFFF"
    )


class RowTintDelegate(QStyledItemDelegate):
    """
    Paints the row highlight.

    This has to be a delegate rather than QTableWidgetItem.setBackground():
    ui/theme.py's app-level stylesheet styles `QTableWidget::item`, and once
    any stylesheet rule targets items, Qt's QStyleSheetStyle draws the cell
    background itself and silently IGNORES the item's brush. setBackground
    still stores the colour -- it just never reaches the screen, which is
    why the tint used to show only on the swatch (a real QToolButton
    painted on top of its cell) and nowhere else.

    For the same reason a tinted cell is painted entirely here rather than
    handed back to super().paint(): that would re-enter the stylesheet
    style, which would fill the cell over the tint again.
    """

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self._page = page

    def paint(self, painter, option, index):
        tint = self._page.tint_for(index.row(), index.column())
        if not tint:
            # Untinted rows keep the app's normal look completely untouched,
            # alternating row colours and selection highlight included.
            super().paint(painter, option, index)
            return

        base = QColor(tint)
        if option.state & QStyle.StateFlag.State_Selected:
            base = _blend(base, QColor(theme_manager.colors()["ACCENT_SOFT"]), _SELECTED_BLEND)
        if index.row() == self._page.hovered_row():
            base = base.darker(_HOVER_DARKEN)

        painter.save()
        painter.fillRect(option.rect, base)

        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text:
            painter.setPen(QColor(readable_text_color(base)))
            painter.setFont(option.font)
            text_rect = option.rect.adjusted(6, 0, -6, 0)
            elided = QFontMetrics(option.font).elidedText(
                str(text), Qt.TextElideMode.ElideRight, text_rect.width()
            )
            alignment = index.data(Qt.ItemDataRole.TextAlignmentRole)
            painter.drawText(
                text_rect,
                int(alignment) if alignment is not None else int(Qt.AlignmentFlag.AlignCenter),
                elided,
            )
        painter.restore()


class CurrentSheetPage(QWidget):
    def __init__(self):
        super().__init__()
        self._settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._displayed_columns = []
        self._displayed_rows = []
        self._populating_table = False
        self._ripple_timer = None
        # The in-flight colour wash: which row, the colour it's coming FROM
        # and going TO, and how many columns have crossed over. The delegate
        # reads this, so the ripple is a property of the paint rather than a
        # stack of one-off brush writes.
        self._ripple = None
        self._hovered_row = -1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(14)

        title = QLabel("Current Sheet")
        apply_live_style(title, lambda c: f"font-size: 18px; font-weight: 700; color: {c['TEXT_PRIMARY']};")
        layout.addWidget(title)

        subtitle = QLabel(
            "Approved timecards, ready to work on. Double-click a cell to edit it; "
            "right-click a row to colour it. Your edits and colours are kept when the inbox is scanned again."
        )
        subtitle.setWordWrap(True)
        apply_live_style(subtitle, lambda c: f"font-size: 12px; color: {c['TEXT_SECONDARY']};")
        layout.addWidget(subtitle)

        # The same Update button as the Export History page -- literally the
        # same workers behind it (ui/sync_workers.py), making the same
        # sync-on/off choice, so clicking it here does exactly what clicking
        # it there does.
        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)

        self.update_btn = QPushButton("Update")
        self.update_btn.setObjectName("secondaryButton")
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.setToolTip(
            "Same Update as on the Export History page: pulls in anything the other "
            "user has sent, pushes this device's data out, and tops up the export sheet."
        )
        self.update_btn.clicked.connect(self._on_update_clicked)
        controls_row.addWidget(self.update_btn)

        self.finalize_btn = QPushButton("Finalize")
        self.finalize_btn.setObjectName("primaryButton")
        self.finalize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.finalize_btn.setToolTip(
            "Same Finalize as on the Export History page: closes out the current "
            "export sheet and starts a new one."
        )
        self.finalize_btn.clicked.connect(self._on_finalize_clicked)
        controls_row.addWidget(self.finalize_btn)

        controls_row.addStretch()
        layout.addLayout(controls_row)

        self.status_label = QLabel("")
        apply_live_style(self.status_label, lambda c: f"font-size: 12px; color: {c['TEXT_SECONDARY']};")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 0)
        configure_grid(self.table)
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setItemDelegate(RowTintDelegate(self, self.table))
        # Hover is tracked per ROW, not per cell: selection is row-wide
        # (SelectRows), so a hover cue on one cell of a row would read as a
        # different, narrower thing. entered() only fires with mouse
        # tracking on; the event filter clears the row on the way out,
        # which entered() never reports.
        self.table.setMouseTracking(True)
        self.table.entered.connect(self._on_cell_entered)
        self.table.viewport().installEventFilter(self)
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
    # Loading / rendering
    # -----------------------------------------------------------------
    def refresh(self):
        # Row indices are about to change; a wash still pointing at an old
        # one would tint whatever row happens to land there.
        self._cancel_ripple()

        rows = get_current_sheet_rows()
        columns = order_columns(rows[0].keys()) if rows else []
        # row_color and its stamps are state, not data -- the colour shows
        # as the row's background and in the picker button, so columns of
        # hex strings and timestamps beside it would just be noise.
        hidden = {"row_color", "color_updated_at", "color_updated_by"}
        columns = [column for column in columns if column not in hidden]

        self._displayed_rows = rows
        self._displayed_columns = columns

        self._populating_table = True
        try:
            # The picker column is built into the header list, NOT added
            # afterwards with insertColumn(). insertColumn shifts existing
            # cell widgets one to the right instead of replacing them, so
            # every refresh left the previous run's picker behind and added
            # a fresh one beside it -- a row grew an extra swatch per
            # refresh. Setting the full column list up front has nothing to
            # shift.
            header_labels = [""] + [HEADER_LABELS.get(c, c) for c in columns]
            self.table.setColumnCount(len(header_labels))
            self.table.setHorizontalHeaderLabels(header_labels)
            # Emptying the table first drops the previous run's cell widgets
            # with their rows; clearContents() alone leaves widgets behind.
            self.table.setRowCount(0)
            self.table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                for col_index, column in enumerate(columns):
                    value = row.get(column)
                    item = QTableWidgetItem("" if value is None else str(value))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if column in _READ_ONLY_COLUMNS:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(row_index, col_index + _DATA_COLUMN_OFFSET, item)
                # A real (empty, non-editable) item under the button too, so
                # the reveal/ripple code can treat every column alike.
                spacer = QTableWidgetItem("")
                spacer.setFlags(spacer.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row_index, _COLOR_COLUMN, spacer)
                self._install_color_button(row_index, row)
        finally:
            self._populating_table = False

        fit_columns(self.table)
        # After fit_columns, which would otherwise size this column to a
        # header that's deliberately blank.
        self.table.setColumnWidth(_COLOR_COLUMN, _COLOR_COLUMN_WIDTH)
        reveal_rows(self.table)
        self.table.setEnabled(True)
        if not rows:
            self.status_label.setText(
                "Nothing here yet - scan the inbox for approved timecards and they'll appear."
            )

    def _install_color_button(self, row_index, record):
        """Puts the row's colour picker in the leading column: ONE small
        circle per row that drops down the preset palette. The circle shows
        the row's current colour, so the column doubles as a legend you can
        scan down even when the row backgrounds are subtle.

        Wrapped in a centring container because a cell widget is stretched
        to fill its cell -- a fixed-size circle on its own would sit pinned
        to the top-left corner rather than centred in the column."""
        button = QToolButton()
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setFixedSize(_SWATCH_DIAMETER, _SWATCH_DIAMETER)
        button.setToolTip("Highlight this row")
        button.setMenu(self._build_palette_menu(row_index, record))
        self._style_color_button(button, record.get("row_color"))

        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)
        self.table.setCellWidget(row_index, _COLOR_COLUMN, holder)

    def swatch_button(self, row_index):
        """The colour circle for a row, or None. Reaches through the
        centring container _install_color_button wraps it in, so callers
        don't have to know that container exists."""
        holder = self.table.cellWidget(row_index, _COLOR_COLUMN)
        if holder is None:
            return None
        return holder.findChild(QToolButton)

    def _build_palette_menu(self, row_index, record):
        menu = QMenu(self)
        for label, hex_color in HIGHLIGHT_PALETTE:
            action = menu.addAction(label)
            if hex_color:
                pixmap = QPixmap(14, 14)
                pixmap.fill(QColor(hex_color))
                action.setIcon(QIcon(pixmap))
            # record, not row_index, is what identifies the row to save --
            # but the paint needs the on-screen index, and both are captured
            # per-action here rather than read at click time, when the
            # loop variables would long since have moved on.
            action.triggered.connect(
                lambda checked=False, r=row_index, rec=record, c=hex_color: self._set_color(r, rec, c)
            )
        return menu

    def _style_color_button(self, button, hex_color):
        """A circle, not a rounded box: border-radius of half the width on a
        square button. menu-indicator is blanked -- QToolButton would
        otherwise draw a little dropdown arrow across the swatch."""
        radius = _SWATCH_DIAMETER // 2
        if hex_color:
            button.setStyleSheet(
                f"QToolButton {{ background: {hex_color}; border: 1px solid rgba(0,0,0,0.35);"
                f" border-radius: {radius}px; }}"
                f"QToolButton::menu-indicator {{ image: none; width: 0px; }}"
            )
        else:
            # No colour set: a hollow ring rather than a filled circle, so
            # "not highlighted" doesn't read as "highlighted in grey".
            apply_live_style(button, lambda c: (
                f"QToolButton {{ background: transparent; border: 1px dashed {c['BORDER']};"
                f" border-radius: {radius}px; }}"
                f"QToolButton::menu-indicator {{ image: none; width: 0px; }}"
            ))

    # -----------------------------------------------------------------
    # What the delegate paints
    # -----------------------------------------------------------------
    def tint_for(self, row_index, column_index):
        """The highlight colour for one cell, or None for "leave it to the
        normal theme styling".

        Normally a whole-row property, read straight off the record. The
        exception is a row mid-ripple: there the columns the wash has
        already reached show the new colour and the rest still show the old
        one, which is the entire effect."""
        ripple = self._ripple
        if ripple is not None and ripple["row"] == row_index:
            return ripple["new"] if column_index < ripple["done"] else ripple["old"]

        if 0 <= row_index < len(self._displayed_rows):
            return self._displayed_rows[row_index].get("row_color")
        return None

    def hovered_row(self):
        return self._hovered_row

    def _on_cell_entered(self, index):
        self._set_hovered_row(index.row())

    def _set_hovered_row(self, row_index):
        if row_index == self._hovered_row:
            return
        self._hovered_row = row_index
        self.table.viewport().update()

    def eventFilter(self, watched, event):
        # entered() never fires for "the cursor left the table", so without
        # this the last hovered row would stay darkened after the mouse has
        # gone somewhere else entirely.
        if watched is self.table.viewport() and event.type() == QEvent.Type.Leave:
            self._set_hovered_row(-1)
        return super().eventFilter(watched, event)

    # -----------------------------------------------------------------
    # Cell editing
    # -----------------------------------------------------------------
    def _on_item_changed(self, item):
        """Persists a double-click cell edit. Fires on every setItem too, so
        the _populating_table guard filters those out. An edit that can't be
        saved is rolled back in the cell so the grid never shows a value the
        database doesn't hold. Same shape as Dashboard._on_item_changed."""
        if self._populating_table:
            return

        row_index, col_index = item.row(), item.column()
        if col_index == _COLOR_COLUMN:
            return  # the picker column holds a button, not an editable value

        data_index = col_index - _DATA_COLUMN_OFFSET
        if row_index >= len(self._displayed_rows) or data_index >= len(self._displayed_columns):
            return

        record = self._displayed_rows[row_index]
        column = self._displayed_columns[data_index]
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
                self.status_label.setText(f"{column} has to be a number - the edit was undone.")
                return

        if update_current_sheet_field(record.get("id"), column, new_value):
            record[column] = new_value
            self.status_label.setText(f"Saved {column}.")
        else:
            revert()
            self.status_label.setText(f"Couldn't save {column} - the edit was undone.")

    # -----------------------------------------------------------------
    # Row colouring (right-click). Cosmetic and entirely user-chosen.
    # -----------------------------------------------------------------
    def _on_context_menu(self, position):
        """Right-click is a second way into the very same palette the
        leading column's button drops down -- not a different set of
        colours, and not a free-form picker, so there's one highlight
        vocabulary however you reach it."""
        item = self.table.itemAt(position)
        if item is None:
            return
        row_index = item.row()
        if row_index >= len(self._displayed_rows):
            return

        menu = self._build_palette_menu(row_index, self._displayed_rows[row_index])
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _set_color(self, row_index, record, hex_color):
        if not set_current_sheet_row_color(record.get("id"), hex_color):
            QMessageBox.warning(
                self, "Couldn't save the colour",
                "That row is no longer in the Current Sheet - try refreshing the page.",
            )
            return
        previous_color = record.get("row_color")
        record["row_color"] = hex_color
        button = self.swatch_button(row_index)
        if button is not None:
            self._style_color_button(button, hex_color)
        # Washed across the row cell by cell rather than snapping, so it's
        # obvious WHICH row was just marked -- with a dozen similar-looking
        # rows on screen, an instant repaint is easy to miss entirely.
        self._start_ripple(row_index, previous_color, hex_color)

    def _start_ripple(self, row_index, old_color, new_color):
        """Runs the colour wash left-to-right across one row.

        Only advances a counter and asks for a repaint -- tint_for() is
        what turns that counter into the two-tone row the delegate draws.
        The record already holds the new colour by this point, so the
        ripple is pure presentation: if it's cancelled, interrupted, or
        never runs at all, the row still ends up correctly coloured."""
        self._cancel_ripple()

        column_count = self.table.columnCount()
        if column_count == 0:
            return

        self._ripple = {"row": row_index, "old": old_color, "new": new_color, "done": 0}

        timer = QTimer(self)
        timer.setInterval(_RIPPLE_STEP_MS)

        def _step():
            ripple = self._ripple
            if ripple is None:
                timer.stop()
                return
            ripple["done"] += 1
            self.table.viewport().update()
            if ripple["done"] >= column_count:
                timer.stop()
                if self._ripple_timer is timer:
                    self._ripple_timer = None
                self._ripple = None

        timer.timeout.connect(_step)
        self._ripple_timer = timer
        _step()  # first column crosses over immediately, so nothing lags
        timer.start()

    def _cancel_ripple(self):
        """Ends any wash in flight and lands the row on its final colour.
        Colouring two rows quickly, or repopulating the table mid-wash,
        must not leave a row stuck showing half of its previous colour."""
        if self._ripple_timer is not None:
            self._ripple_timer.stop()
            self._ripple_timer = None
        if self._ripple is not None:
            self._ripple = None
            self.table.viewport().update()

    # -----------------------------------------------------------------
    # Update (the Export History page's button, same workers)
    # -----------------------------------------------------------------
    def _sync_enabled(self):
        return self._settings.value(SYNC_ENABLED_KEY, True, type=bool)

    def _set_controls_enabled(self, enabled):
        """Both buttons move together: Finalize runs an Update first, so
        letting one start while the other is mid-flight would have two
        scans writing at once."""
        self.update_btn.setEnabled(enabled)
        self.finalize_btn.setEnabled(enabled)

    def _on_update_clicked(self):
        self._set_controls_enabled(False)

        if self._sync_enabled():
            self.status_label.setText("Updating...")
            self._update_worker = UpdateWorker(
                sync_partner_settings.partner_email, project_type_settings.project_type,
            )
        else:
            self.status_label.setText("Updating (sync is off - local scan only)...")
            self._update_worker = LocalUpdateWorker()

        self._update_thread = QThread(self)
        self._update_worker.moveToThread(self._update_thread)

        self._update_thread.started.connect(self._update_worker.run)
        self._update_worker.progress.connect(self.status_label.setText)
        self._update_worker.finished.connect(self._on_update_finished)
        self._update_worker.failed.connect(self._on_update_failed)
        self._update_worker.finished.connect(self._update_thread.quit)
        self._update_worker.failed.connect(self._update_thread.quit)
        self._update_thread.finished.connect(self._update_thread.deleteLater)

        self._update_thread.start()

    def _on_update_finished(self, _result):
        self._set_controls_enabled(True)
        self.status_label.setText("Update finished.")
        # A scan can only ever ADD rows here, so this brings in whatever is
        # new without disturbing anything already on screen.
        self.refresh()

    def _on_update_failed(self, message):
        self._set_controls_enabled(True)
        self.status_label.setText("Update failed - nothing was changed.")
        QMessageBox.warning(self, "Couldn't finish", message)

    # -----------------------------------------------------------------
    # Finalize (the Export History page's button, same workers)
    # -----------------------------------------------------------------
    def _finalize_period(self):
        """The range Finalize closes out.

        The Export History page takes this from its two date pickers; this
        page has none, so it uses the period that's actually open: from the
        last export's boundary (get_last_export_date, the same value that
        page's "From last export" button offers) through today. Falling
        back to the first of the current month when nothing has ever been
        exported."""
        end = QDate.currentDate()
        last_export = get_last_export_date()
        if last_export:
            start = QDate.fromString(last_export, "yyyy-MM-dd")
            if start.isValid():
                return start.toString("yyyy-MM-dd"), end.toString("yyyy-MM-dd")
        return end.addDays(-(end.day() - 1)).toString("yyyy-MM-dd"), end.toString("yyyy-MM-dd")

    def _on_finalize_clicked(self):
        sync_on = self._sync_enabled()
        email = sync_partner_settings.partner_email if sync_on else None
        start_str, end_str = self._finalize_period()

        active_path = get_active_export_path()
        sheet_text = (
            f"{active_path} is closed as the final export"
            if active_path else "the export sheet is created and closed"
        )
        message = f"This closes out {start_str} to {end_str}.\n\n"
        if not sync_on:
            message += (
                f"One last local scan runs first, then {sheet_text}.\n\n"
                "Sync is currently off, so this closes out the period locally only - "
                "no other user will be notified.\n\n"
            )
        elif email:
            message += (
                f"One last Update runs first, then {sheet_text} and {email} is "
                "notified so both apps agree the period is closed.\n\n"
            )
        else:
            message += (
                f"One last Update runs first, then {sheet_text}.\n\n"
                "No sync partner is set, so nobody will be notified - the period "
                "closes on this machine only.\n\n"
            )
        message += (
            "From then on, Update starts filling a NEW sheet -- this one won't "
            "be added to again.\n\n"
            "Are you sure you want to finalize?"
        )

        confirm = QMessageBox.question(
            self, "Finalize this period?", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._set_controls_enabled(False)
        self.status_label.setText("Finalizing...")

        project_type = project_type_settings.project_type
        if sync_on:
            self._finalize_worker = FinalizeWorker(email, start_str, end_str, project_type)
        else:
            self._finalize_worker = LocalFinalizeWorker(start_str, end_str, project_type)

        self._finalize_thread = QThread(self)
        self._finalize_worker.moveToThread(self._finalize_thread)

        self._finalize_thread.started.connect(self._finalize_worker.run)
        self._finalize_worker.progress.connect(self.status_label.setText)
        self._finalize_worker.finished.connect(self._on_finalize_finished)
        self._finalize_worker.failed.connect(self._on_finalize_failed)
        self._finalize_worker.finished.connect(self._finalize_thread.quit)
        self._finalize_worker.failed.connect(self._finalize_thread.quit)
        self._finalize_thread.finished.connect(self._finalize_thread.deleteLater)

        self._finalize_thread.start()

    def _on_finalize_finished(self, result):
        self._set_controls_enabled(True)
        row_count = result.get("row_count", 0)
        path = result.get("path", "")
        self.status_label.setText(
            f"Finalized {path} - {row_count} row(s). The next Update starts a new sheet."
        )
        QMessageBox.information(
            self, "Finalized",
            f"Closed {path} with {row_count} row(s).\n\n"
            "The next Update will create and start filling a new sheet.",
        )
        self.refresh()

    def _on_finalize_failed(self, message):
        self._set_controls_enabled(True)
        self.status_label.setText("Finalize failed - nothing was changed.")
        QMessageBox.warning(self, "Couldn't finish", message)

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)


