from datetime import date, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QDateEdit, QFileDialog, QMessageBox, QButtonGroup,
    QDialog, QMenu,
)
from PySide6.QtCore import Qt, QDate, QThread, QObject, Signal
from PySide6.QtGui import QBrush, QColor

from ui.theme_utils import apply_live_style
from ui.project_type_settings import project_type_settings
from ui.sharepoint_settings import sharepoint_settings
from storage_service import (
    get_export_history, export_act_invoice_overview_range, get_last_export_date,
    PROJECT_TYPE_LABELS, SNAPSHOT_COLUMNS, get_device_id, find_record_id,
    update_status_record_field,
)
from sync_service import sharepoint_update, sharepoint_view_current, sharepoint_finalize
from sharepoint_service import SharePointFolderError

# Reverse of storage_service._STATUS_LABELS -- that dict is private
# (keyed the other way, for update_status_record_field's Dashboard-style
# caller); a merged current-sheet row's "status" field already holds the
# Title-case label ("Approved"/"Pending"/"Rejected", see
# storage_service._row_dict_to_entry), so this file needs the reverse.
_STATUS_LABEL_TO_KEY = {"Approved": "approve", "Pending": "pending", "Rejected": "reject"}

# Preset highlight palette for the SharePoint View Current window -- same
# idea as the old Current Sheet page's row-tag colors. Kept small and
# fixed rather than a full color picker.
_HIGHLIGHT_PALETTE = [
    ("None", None),
    ("Red", "#E05252"),
    ("Orange", "#FF7A00"),
    ("Yellow", "#FFEE33"),
    ("Green", "#4CAF50"),
    ("Blue", "#4A90D9"),
    ("Purple", "#9B59B6"),
]

# Goes into the default filename of a division-only export, so the two
# divisions' files don't land on top of each other in the save dialog.
_FILENAME_SLUGS = {
    "beverage": "food_beverage",
    "hospitality": "hospitality",
}

# Accent used for the QDateEdit calendar popups on this page, so they read
# as the same "orange" filter UI as the Records page's date filter instead
# of Qt's default blue.
_CALENDAR_ACCENT = "#FF7A00"


def _last_month_range(today=None):
    """Returns (first_day, last_day) of the calendar month before today,
    as date objects."""
    today = today or date.today()
    first_of_this_month = today.replace(day=1)
    last_day_prev_month = first_of_this_month - timedelta(days=1)
    first_day_prev_month = last_day_prev_month.replace(day=1)
    return first_day_prev_month, last_day_prev_month


def _apply_orange_calendar_style(date_edit):
    """Restyles a QDateEdit's popup QCalendarWidget (created lazily once
    setCalendarPopup(True) is set) to use the app's orange accent instead
    of Qt's default blue, so it visually matches the Records page's date
    filter. Purely cosmetic -- the QDateEdit/QCalendarWidget widgets and
    all the .date()/.setDate() calls elsewhere in this file are untouched,
    so none of the export/finalize logic changes."""
    calendar = date_edit.calendarWidget()
    apply_live_style(calendar, lambda c: f"""
        QCalendarWidget QWidget {{
            background-color: {c['SURFACE']}; color: {c['TEXT_PRIMARY']};
        }}
        QCalendarWidget QToolButton {{
            background-color: transparent; color: {c['TEXT_PRIMARY']};
            font-weight: 700; font-size: 13px; icon-size: 16px; padding: 4px;
        }}
        QCalendarWidget QToolButton:hover {{
            background-color: {_CALENDAR_ACCENT}; color: white; border-radius: 4px;
        }}
        QCalendarWidget QMenu {{
            background-color: {c['SURFACE']}; color: {c['TEXT_PRIMARY']};
        }}
        QCalendarWidget QSpinBox {{
            background-color: {c['SURFACE']}; color: {c['TEXT_PRIMARY']};
            selection-background-color: {_CALENDAR_ACCENT};
        }}
        #qt_calendar_navigationbar {{
            background-color: {c['SURFACE']};
        }}
        QCalendarWidget QAbstractItemView:enabled {{
            background-color: {c['BG']}; color: {c['TEXT_PRIMARY']};
            selection-background-color: {_CALENDAR_ACCENT}; selection-color: white;
        }}
        QCalendarWidget QAbstractItemView:disabled {{
            color: {c['TEXT_SECONDARY']};
        }}
    """)


class _SharePointUpdateWorker(QObject):
    progress = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, folder, project_type):
        super().__init__()
        self.folder = folder
        self.project_type = project_type

    def run(self):
        try:
            result = sharepoint_update(
                self.folder, progress_callback=self.progress.emit, project_type=self.project_type,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


class _SharePointViewWorker(QObject):
    progress = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, folder, project_type):
        super().__init__()
        self.folder = folder
        self.project_type = project_type

    def run(self):
        try:
            result = sharepoint_view_current(
                self.folder, progress_callback=self.progress.emit, project_type=self.project_type,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


class _SharePointFinalizeWorker(QObject):
    progress = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, folder, project_type):
        super().__init__()
        self.folder = folder
        self.project_type = project_type

    def run(self):
        try:
            result = sharepoint_finalize(
                self.folder, progress_callback=self.progress.emit, project_type=self.project_type,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


class _HighlightButton(QPushButton):
    """One row's highlight swatch. Clickable (opens a color menu) only
    for rows this device owns -- disabled/static for rows read in from
    another device's file, since there's no local record here to attach
    the tag to."""

    def __init__(self, color, editable, on_pick, parent=None):
        super().__init__(parent)
        self.setFixedSize(20, 20)
        self._on_pick = on_pick
        self.set_color(color)
        if editable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Click to tag this row with a color")
            self.clicked.connect(self._open_menu)
        else:
            self.setEnabled(False)
            self.setToolTip("Scanned by another device -- read-only here")

    def set_color(self, color):
        self._color = color
        bg = color or "transparent"
        border = "1px solid rgba(128,128,128,0.6)"
        self.setStyleSheet(f"QPushButton {{ background-color: {bg}; border: {border}; border-radius: 4px; }}")

    def _open_menu(self):
        menu = QMenu(self)
        for name, color in _HIGHLIGHT_PALETTE:
            action = menu.addAction(name)
            action.triggered.connect(lambda checked=False, c=color: self._pick(c))
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))

    def _pick(self, color):
        self.set_color(color)
        self._on_pick(color)


class _CurrentSheetDialog(QDialog):
    """Shown by View Current -- the merged, deduped rows from every
    device's current_*.xlsx (which sharepoint_view_current already
    refreshed by running Update first, so this is always the latest
    shared data at the moment the dialog opens).

    Only rate and highlight-color are editable, and only on rows THIS
    device originally scanned (a local DB record exists to attach the
    edit to -- see storage_service.find_record_id). Rows scanned by
    another device show read-only, since there's nothing local here to
    edit.

    Edits are held in memory until the window closes: closing with
    pending edits asks Save/Discard/Cancel. Save writes each edit to the
    local DB (storage_service.update_status_record_field) and then runs
    sharepoint_update once more so this device's current_<device_id>.xlsx
    -- and therefore the shared folder -- reflects the edits immediately,
    rather than waiting for the next unrelated Update click.
    """

    def __init__(self, rows, sources, folder, project_type, parent=None):
        super().__init__(parent)
        self._folder = folder
        self._project_type = project_type
        self._rows = rows
        self._edits = {}  # row_index -> {"row": <merged row dict>, "changes": {column: value}}
        self._populating = True

        self.setWindowTitle("Current Sheet (merged, all devices)")
        self.resize(1050, 540)
        layout = QVBoxLayout(self)

        info = QLabel(f"Merged from: {', '.join(sources) if sources else '(no device sheets found)'}")
        info.setWordWrap(True)
        apply_live_style(info, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 12px;")
        layout.addWidget(info)

        hint = QLabel("Rate and the highlight column are editable on rows this device scanned itself. Other rows are read-only.")
        hint.setWordWrap(True)
        apply_live_style(hint, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 11px;")
        layout.addWidget(hint)

        this_device = get_device_id()
        headers = ["•"] + [label for _key, label in SNAPSHOT_COLUMNS]
        self._rate_col_index = 1 + [key for key, _label in SNAPSHOT_COLUMNS].index("rate")

        self.table = QTableWidget(len(rows), len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 32)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed
        )
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

        for row_index, row in enumerate(rows):
            is_own = bool(this_device) and row.get("_origin_device_id") == this_device

            button = _HighlightButton(
                row.get("highlight_color"), is_own,
                lambda color, r=row_index: self._on_highlight_picked(r, color),
            )
            wrapper = QWidget()
            wrapper_layout = QHBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(0, 0, 0, 0)
            wrapper_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            wrapper_layout.addWidget(button)
            self.table.setCellWidget(row_index, 0, wrapper)

            for col_offset, (key, _label) in enumerate(SNAPSHOT_COLUMNS):
                col_index = col_offset + 1
                value = row.get(key)
                item = QTableWidgetItem("" if value is None else str(value))
                editable = is_own and key == "rate"
                flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                if editable:
                    flags |= Qt.ItemFlag.ItemIsEditable
                else:
                    item.setForeground(QBrush(QColor(150, 150, 150)))
                    if not is_own:
                        item.setToolTip("Scanned by another device -- read-only here")
                item.setFlags(flags)
                item.setData(Qt.ItemDataRole.UserRole, row_index)
                self.table.setItem(row_index, col_index, item)

        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, stretch=1)
        self._populating = False

        self.status_label = QLabel("")
        apply_live_style(self.status_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 12px;")
        layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondaryButton")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)

    def _mark_dirty(self, row_index, column, value):
        entry = self._edits.setdefault(row_index, {"row": self._rows[row_index], "changes": {}})
        entry["changes"][column] = value
        self.status_label.setText(f"{len(self._edits)} row(s) with unsaved changes.")

    def _on_highlight_picked(self, row_index, color):
        self._mark_dirty(row_index, "highlight_color", color)

    def _on_item_changed(self, item):
        if self._populating or item.column() != self._rate_col_index:
            return
        row_index = item.data(Qt.ItemDataRole.UserRole)
        text = item.text().strip()
        try:
            new_rate = float(text) if text else 0.0
        except ValueError:
            QMessageBox.warning(self, "Invalid rate", "Rate must be a number.")
            self._populating = True
            item.setText(str(self._rows[row_index].get("rate") or 0))
            self._populating = False
            return
        self._mark_dirty(row_index, "rate", new_rate)

    def _save_pending_edits(self):
        for edit in self._edits.values():
            row = edit["row"]
            status_key = _STATUS_LABEL_TO_KEY.get(row.get("status"))
            if status_key is None:
                continue
            record_id = find_record_id(
                row.get("status"), row.get("day"), row.get("project_code"),
                row.get("task"), row.get("person_number"),
                row.get("received", "")[:7] if row.get("received") else None,
            )
            if record_id is None:
                continue  # shouldn't happen -- only own rows are editable, and own rows have a local record
            for column, value in edit["changes"].items():
                update_status_record_field(status_key, record_id, column, value)

        try:
            sharepoint_update(self._folder, project_type=self._project_type)
        except SharePointFolderError as exc:
            raise RuntimeError(
                f"Changes were saved to the database, but re-publishing the shared sheet failed: {exc}"
            ) from exc

    def closeEvent(self, event):
        if not self._edits:
            event.accept()
            return

        choice = QMessageBox.question(
            self, "Confirm changes",
            f"You have unsaved changes to {len(self._edits)} row(s). Save them to the shared sheet before closing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            event.ignore()
            return
        if choice == QMessageBox.StandardButton.Discard:
            event.accept()
            return

        try:
            self._save_pending_edits()
        except Exception as exc:
            QMessageBox.warning(self, "Couldn't save changes", str(exc))
            event.ignore()
            return

        event.accept()


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

        self._project_type_group = QButtonGroup(self)
        self._project_type_group.setExclusive(True)
        self._project_type_buttons = {}  # project_type -> button, for the sync handler below

        type_defs = [(None, "All")] + list(PROJECT_TYPE_LABELS.items())
        for project_type, label in type_defs:
            button = QPushButton(label)
            button.setObjectName("periodToggle")
            button.setCheckable(True)
            # Reflects whatever's already selected (possibly by the
            # Dashboard, or from a previous session) rather than always
            # defaulting to "All" regardless of shared state.
            button.setChecked(project_type == project_type_settings.project_type)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.toggled.connect(
                lambda checked, value=project_type: self._on_project_type_toggled(value) if checked else None
            )
            self._project_type_group.addButton(button)
            self._project_type_buttons[project_type] = button
            type_row.addWidget(button)

        # Keeps this page's buttons in lockstep with the Dashboard's
        # (and vice versa) - toggling one is what fires this, on both pages.
        project_type_settings.project_type_changed.connect(self._sync_project_type_selection)

        type_row.addStretch()
        layout.addLayout(type_row)

        # --- SharePoint file sync controls (Update / View Current / Finalize) ---
        # The app's only cross-device sync channel -- see
        # services/sync_service.py's sharepoint_update/
        # sharepoint_view_current/sharepoint_finalize and
        # SHAREPOINT_SYNC_SPEC.md. Reads/writes files in a shared, locally-
        # synced OneDrive/SharePoint folder (set in Settings -> SharePoint).
        sharepoint_section_label = QLabel("SharePoint Sync (shared folder)")
        apply_live_style(sharepoint_section_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 11px; font-weight: 700;")
        layout.addWidget(sharepoint_section_label)

        sharepoint_row = QHBoxLayout()
        sharepoint_row.setSpacing(10)

        self.sharepoint_update_btn = QPushButton("SharePoint Update")
        self.sharepoint_update_btn.setObjectName("secondaryButton")
        self.sharepoint_update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sharepoint_update_btn.setToolTip(
            "Rebuilds THIS device's own current-sheet file from the database and writes "
            "it into the shared SharePoint folder. Safe to click repeatedly."
        )
        self.sharepoint_update_btn.clicked.connect(self._on_sharepoint_update_clicked)
        sharepoint_row.addWidget(self.sharepoint_update_btn)

        self.sharepoint_view_btn = QPushButton("View Current")
        self.sharepoint_view_btn.setObjectName("secondaryButton")
        self.sharepoint_view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sharepoint_view_btn.setToolTip(
            "Runs SharePoint Update first, then shows the merged current sheet from every "
            "device's file in the shared folder. Preview only -- changes nothing."
        )
        self.sharepoint_view_btn.clicked.connect(self._on_sharepoint_view_clicked)
        sharepoint_row.addWidget(self.sharepoint_view_btn)

        self.sharepoint_finalize_btn = QPushButton("SharePoint Finalize")
        self.sharepoint_finalize_btn.setObjectName("primaryButton")
        self.sharepoint_finalize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sharepoint_finalize_btn.setToolTip(
            "Prints the merged current sheet (every device) and resets the shared folder's "
            "period boundary -- closes the period for every device, not just this one."
        )
        self.sharepoint_finalize_btn.clicked.connect(self._on_sharepoint_finalize_clicked)
        sharepoint_row.addWidget(self.sharepoint_finalize_btn)

        sharepoint_row.addStretch()
        layout.addLayout(sharepoint_row)

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

        # Fills the "from" picker with the received-date the last export
        # reached, so a follow-up export can start where the previous one left
        # off (the "to" is still picked by hand). Disabled until something has
        # been exported -- there's no "last export" to start from yet.
        self.last_export_btn = QPushButton("From last export")
        self.last_export_btn.setObjectName("secondaryButton")
        self.last_export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.last_export_btn.clicked.connect(self._use_last_export_start)
        controls_row.addWidget(self.last_export_btn)

        date_edit_style = lambda c: f"""
            QDateEdit {{
                border: 1px solid {c['BORDER']};
                border-radius: 6px;
                padding: 6px 8px;
                font-size: 13px;
                background: {c['SURFACE']};
                color: {c['TEXT_PRIMARY']};
            }}
            QDateEdit:focus {{ border: 1px solid {_CALENDAR_ACCENT}; }}
            QDateEdit::drop-down {{ border: none; width: 18px; }}
        """

        self.from_date = QDateEdit()
        self.from_date.setCalendarPopup(True)
        self.from_date.setDisplayFormat("yyyy-MM-dd")
        self.from_date.setDate(QDate.currentDate().addMonths(-1))
        apply_live_style(self.from_date, date_edit_style)
        _apply_orange_calendar_style(self.from_date)
        controls_row.addWidget(self.from_date)

        to_label = QLabel("to")
        apply_live_style(to_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 13px;")
        controls_row.addWidget(to_label)

        self.to_date = QDateEdit()
        self.to_date.setCalendarPopup(True)
        self.to_date.setDisplayFormat("yyyy-MM-dd")
        self.to_date.setDate(QDate.currentDate())
        apply_live_style(self.to_date, date_edit_style)
        _apply_orange_calendar_style(self.to_date)
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
    def _on_project_type_toggled(self, project_type):
        project_type_settings.set_project_type(project_type)

    def _sync_project_type_selection(self, project_type):
        """Fires whenever EITHER page's project-type filter changes -
        including from this page's own toggle above, in which case the
        matching button is already checked and this is a no-op."""
        button = self._project_type_buttons.get(project_type)
        if button is not None:
            button.setChecked(True)

    # -----------------------------------------------------------------
    # SharePoint file sync: Update / View Current / Finalize
    # -----------------------------------------------------------------
    def _require_sharepoint_folder(self):
        folder = sharepoint_settings.folder
        if not folder:
            QMessageBox.warning(
                self, "No SharePoint folder set",
                "Set a locally-synced SharePoint/OneDrive folder path in Settings first "
                "(Settings -> SharePoint) before using Update, View Current, or Finalize.",
            )
            return None
        return folder

    def _set_sharepoint_controls_enabled(self, enabled):
        self.sharepoint_update_btn.setEnabled(enabled)
        self.sharepoint_view_btn.setEnabled(enabled)
        self.sharepoint_finalize_btn.setEnabled(enabled)

    def _on_sharepoint_failed(self, message):
        self._set_sharepoint_controls_enabled(True)
        self.status_label.setText("SharePoint action failed.")
        QMessageBox.warning(self, "SharePoint sync failed", message)

    def _on_sharepoint_update_clicked(self):
        folder = self._require_sharepoint_folder()
        if not folder:
            return
        self._set_sharepoint_controls_enabled(False)
        self.status_label.setText("SharePoint: updating...")

        self._sp_update_thread = QThread(self)
        self._sp_update_worker = _SharePointUpdateWorker(folder, project_type_settings.project_type)
        self._sp_update_worker.moveToThread(self._sp_update_thread)

        self._sp_update_thread.started.connect(self._sp_update_worker.run)
        self._sp_update_worker.progress.connect(self.status_label.setText)
        self._sp_update_worker.finished.connect(self._on_sharepoint_update_finished)
        self._sp_update_worker.failed.connect(self._on_sharepoint_failed)
        self._sp_update_worker.finished.connect(self._sp_update_thread.quit)
        self._sp_update_worker.failed.connect(self._sp_update_thread.quit)
        self._sp_update_thread.finished.connect(self._sp_update_thread.deleteLater)

        self._sp_update_thread.start()

    def _on_sharepoint_update_finished(self, result):
        self._set_sharepoint_controls_enabled(True)
        self.status_label.setText(f"SharePoint Update: wrote {result.get('rows', 0)} row(s) to the shared folder.")

    def _on_sharepoint_view_clicked(self):
        folder = self._require_sharepoint_folder()
        if not folder:
            return
        self._set_sharepoint_controls_enabled(False)
        self.status_label.setText("SharePoint: updating and merging...")

        self._sp_view_folder = folder
        self._sp_view_project_type = project_type_settings.project_type

        self._sp_view_thread = QThread(self)
        self._sp_view_worker = _SharePointViewWorker(folder, project_type_settings.project_type)
        self._sp_view_worker.moveToThread(self._sp_view_thread)

        self._sp_view_thread.started.connect(self._sp_view_worker.run)
        self._sp_view_worker.progress.connect(self.status_label.setText)
        self._sp_view_worker.finished.connect(self._on_sharepoint_view_finished)
        self._sp_view_worker.failed.connect(self._on_sharepoint_failed)
        self._sp_view_worker.finished.connect(self._sp_view_thread.quit)
        self._sp_view_worker.failed.connect(self._sp_view_thread.quit)
        self._sp_view_thread.finished.connect(self._sp_view_thread.deleteLater)

        self._sp_view_thread.start()

    def _on_sharepoint_view_finished(self, result):
        self._set_sharepoint_controls_enabled(True)
        rows = result.get("rows", [])
        sources = result.get("sources", [])
        self.status_label.setText(f"View Current: {len(rows)} row(s) merged from {len(sources)} device sheet(s).")
        dialog = _CurrentSheetDialog(
            rows, sources, self._sp_view_folder, self._sp_view_project_type, parent=self,
        )
        dialog.exec()

    def _on_sharepoint_finalize_clicked(self):
        folder = self._require_sharepoint_folder()
        if not folder:
            return

        confirm = QMessageBox.question(
            self, "SharePoint Finalize?",
            "This prints the merged current sheet (every device) to your default printer, "
            "then resets the shared boundary so every device's next Update starts a fresh, "
            "empty current sheet -- including a device that never clicked this button.\n\n"
            "If printing fails, nothing changes and you can retry.\n\n"
            "Are you sure you want to finalize?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._set_sharepoint_controls_enabled(False)
        self.status_label.setText("SharePoint: finalizing...")

        self._sp_finalize_thread = QThread(self)
        self._sp_finalize_worker = _SharePointFinalizeWorker(folder, project_type_settings.project_type)
        self._sp_finalize_worker.moveToThread(self._sp_finalize_thread)

        self._sp_finalize_thread.started.connect(self._sp_finalize_worker.run)
        self._sp_finalize_worker.progress.connect(self.status_label.setText)
        self._sp_finalize_worker.finished.connect(self._on_sharepoint_finalize_finished)
        self._sp_finalize_worker.failed.connect(self._on_sharepoint_failed)
        self._sp_finalize_worker.finished.connect(self._sp_finalize_thread.quit)
        self._sp_finalize_worker.failed.connect(self._sp_finalize_thread.quit)
        self._sp_finalize_thread.finished.connect(self._sp_finalize_thread.deleteLater)

        self._sp_finalize_thread.start()

    def _on_sharepoint_finalize_finished(self, result):
        self._set_sharepoint_controls_enabled(True)
        self.status_label.setText(
            f"SharePoint Finalize: printed {result.get('rows_printed', 0)} row(s); "
            f"boundary reset to {result.get('boundary_date')}."
        )
        QMessageBox.information(
            self, "Finalized",
            f"Printed {result.get('rows_printed', 0)} row(s) and reset the shared current sheet "
            f"as of {result.get('boundary_date')} -- every device starts fresh on their next Update.",
        )

    def _use_last_export_start(self):
        """Set the 'from' date to where the last export reached. The 'to'
        date is left as-is for the user to choose before Export Range."""
        last = get_last_export_date()
        if not last:
            QMessageBox.information(
                self, "No previous export",
                "Nothing has been exported yet, so there's no last-export date to start from.",
            )
            return
        self.from_date.setDate(QDate.fromString(last, "yyyy-MM-dd"))
        self.status_label.setText(f"Start set to last export date ({last}). Pick a 'to' date and Export Range.")

    def _default_name(self, start, end):
        """Filename offered in the save dialog. A division-only export says
        which division it is, so two exports of the same date range don't
        default to the same name."""
        slug = _FILENAME_SLUGS.get(project_type_settings.project_type)
        prefix = f"timecards_{slug}" if slug else "timecards"
        return f"{prefix}_{start}_to_{end}.xlsx"

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
        path, _ = QFileDialog.getSaveFileName(self, "Save export as", default_name, "Excel files (*.xlsx)")
        if not path:
            return  # user cancelled

        project_type = project_type_settings.project_type
        row_count = export_act_invoice_overview_range(start, end, path, project_type=project_type)

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
        last = get_last_export_date()
        self.last_export_btn.setEnabled(bool(last))
        self.last_export_btn.setToolTip(
            f"Start a range from {last} (last export)" if last
            else "No export has been done yet"
        )

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