from datetime import date, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QDateEdit, QFileDialog, QMessageBox, QButtonGroup,
)
from PySide6.QtCore import Qt, QDate, QThread, QObject, Signal

from ui.theme_utils import apply_live_style
from ui.project_type_settings import project_type_settings
from storage_service import (
    get_export_history, export_act_invoice_overview_range, get_last_export_date,
    PROJECT_TYPE_LABELS,
)
from sync_service import sync_cards
from date_utils import get_since_last_export_range

# Goes into the default filename of a division-only export, so the two
# divisions' files don't land on top of each other in the save dialog.
_FILENAME_SLUGS = {
    "beverage": "food_beverage",
    "hospitality": "hospitality",
}

# Used as the export range's lower bound on the very first-ever Finalize,
# when there's no previous export to start from -- effectively "everything
# currently in the database", without needing export_act_invoice_overview_range
# to support an open-ended start.
_EPOCH_DATE = "2000-01-01"


def _last_month_range(today=None):
    """Returns (first_day, last_day) of the calendar month before today,
    as date objects."""
    today = today or date.today()
    first_of_this_month = today.replace(day=1)
    last_day_prev_month = first_of_this_month - timedelta(days=1)
    first_day_prev_month = last_day_prev_month.replace(day=1)
    return first_day_prev_month, last_day_prev_month


class _FinalizeWorker(QObject):
    """Runs Finalize's two real steps off the GUI thread: scan the inbox
    for anything new since the last finalize, then export. The save path
    is picked on the GUI thread BEFORE this starts (see
    HistoryPage._on_finalize_clicked) -- QFileDialog has to run on the
    main thread, and there's no reason to make the user wait through a
    scan just to be asked where to save afterward."""
    progress = Signal(str)
    finished = Signal(dict)

    def __init__(self, scan_start, scan_end, export_start, export_end, output_path, project_type):
        super().__init__()
        self.scan_start = scan_start
        self.scan_end = scan_end
        self.export_start = export_start
        self.export_end = export_end
        self.output_path = output_path
        self.project_type = project_type

    def run(self):
        sync_cards(
            progress_callback=self.progress.emit,
            start_date=self.scan_start, end_date=self.scan_end,
        )
        self.progress.emit("Exporting final sheet...")
        # Also advances last_export_date to export_end and logs
        # export_history -- see export_act_invoice_overview_range. That's
        # what "resets the date" automatically: no separate step needed.
        row_count = export_act_invoice_overview_range(
            self.export_start, self.export_end, self.output_path, project_type=self.project_type,
        )
        self.finished.emit({"row_count": row_count})


class HistoryPage(QWidget):
    """Export controls (last month / custom date range, both filtering
    by the date each email was RECEIVED - see storage_service._to_row)
    plus Finalize (scan + export + auto-reset the period boundary) and a
    log of every export ever produced (name + date), newest first."""

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

        # --- Finalize: scans the inbox, updates the database, exports the
        # finalized sheet for [last export -> now], and resets the period
        # boundary -- all in one click, after confirming. ---
        finalize_row = QHBoxLayout()
        finalize_row.setSpacing(10)

        self.finalize_btn = QPushButton("Finalize")
        self.finalize_btn.setObjectName("primaryButton")
        self.finalize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.finalize_btn.clicked.connect(self._on_finalize_clicked)
        finalize_row.addWidget(self.finalize_btn)

        finalize_row.addStretch()
        layout.addLayout(finalize_row)

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
    # Project type sync
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
    # Finalize
    # -----------------------------------------------------------------
    def _on_finalize_clicked(self):
        last = get_last_export_date()
        scan_start, scan_end = get_since_last_export_range(last)
        export_start = scan_start.date().isoformat() if scan_start else _EPOCH_DATE
        export_end = scan_end.date().isoformat()

        project_type = project_type_settings.project_type
        scope_text = f" for {PROJECT_TYPE_LABELS[project_type]}" if project_type else ""

        confirm = QMessageBox.question(
            self, "Finalize this period?",
            f"This scans the inbox for anything new, exports the finalized sheet "
            f"covering {export_start} to {export_end}{scope_text}, and resets the "
            "period boundary to today.\n\n"
            "Are you sure you want to finalize?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save final export as", self._default_name(export_start, export_end), "Excel files (*.xlsx)"
        )
        if not path:
            return  # user cancelled the save dialog -- nothing has happened yet, safe to just stop here

        self.finalize_btn.setEnabled(False)
        self.status_label.setText("Finalizing...")

        self._finalize_thread = QThread(self)
        self._finalize_worker = _FinalizeWorker(
            scan_start, scan_end, export_start, export_end, path, project_type,
        )
        self._finalize_worker.moveToThread(self._finalize_thread)

        self._finalize_thread.started.connect(self._finalize_worker.run)
        self._finalize_worker.progress.connect(self.status_label.setText)
        self._finalize_worker.finished.connect(self._on_finalize_finished)
        self._finalize_worker.finished.connect(self._finalize_thread.quit)
        self._finalize_thread.finished.connect(self._finalize_thread.deleteLater)

        self._finalize_thread.start()

    def _on_finalize_finished(self, result):
        self.finalize_btn.setEnabled(True)
        row_count = result.get("row_count", 0)
        self.status_label.setText(f"Finalized - {row_count} row(s) exported.")
        QMessageBox.information(
            self, "Finalized",
            f"Exported {row_count} row(s). The period has been reset -- "
            "the next Scan Inbox / Finalize starts from today.",
        )
        self.refresh()

    # -----------------------------------------------------------------
    # Export actions
    # -----------------------------------------------------------------
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