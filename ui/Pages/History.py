from datetime import date, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QDateEdit, QFileDialog, QMessageBox, QButtonGroup,
)
from PySide6.QtCore import Qt, QDate, QThread, QObject, Signal

from ui.theme_utils import apply_live_style
from ui.project_type_settings import project_type_settings
from ui.sync_partner_settings import sync_partner_settings
from storage_service import (
    get_export_history, export_act_invoice_overview_range, get_last_export_date,
    get_active_export_path, PROJECT_TYPE_LABELS,
)
from sync_service import update_with_other_user, finalize_month

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


class _UpdateWorker(QObject):
    progress = Signal(str)
    finished = Signal(dict)

    def __init__(self, recipient_email, project_type):
        super().__init__()
        self.recipient_email = recipient_email
        self.project_type = project_type

    def run(self):
        result = update_with_other_user(
            self.recipient_email, project_type=self.project_type,
            progress_callback=self.progress.emit,
        )
        self.finished.emit(result)


class _FinalizeWorker(QObject):
    progress = Signal(str)
    finished = Signal(dict)

    def __init__(self, recipient_email, start_date, end_date, project_type):
        super().__init__()
        self.recipient_email = recipient_email
        self.start_date = start_date
        self.end_date = end_date
        self.project_type = project_type

    def run(self):
        result = finalize_month(
            self.recipient_email, self.start_date, self.end_date,
            project_type=self.project_type, progress_callback=self.progress.emit,
        )
        self.finished.emit(result)


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

        # --- Cross-device sync controls (Update / Finalize) ---
        # Update: pulls in anything the other user has sent, pushes this
        # device's own data out to them, and tops up the active export
        # file with any approved record that isn't in it yet (creating
        # that file the first time) -- so both apps converge on the same
        # live picture of the current, not-yet-finalized period, and the
        # sheet grows with it. It does NOT read the inbox; that's Scan
        # Inbox's job, and Update works off what Scan Inbox stored.
        # Finalize: one last Update pass, then the file being filled is
        # closed out as the final export and the app points at a fresh
        # one -- which is what closes the period on BOTH machines
        # (see services/sync_service.py).
        sync_row = QHBoxLayout()
        sync_row.setSpacing(10)

        self.update_btn = QPushButton("Update")
        self.update_btn.setObjectName("secondaryButton")
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.clicked.connect(self._on_update_clicked)
        sync_row.addWidget(self.update_btn)

        self.finalize_btn = QPushButton("Finalize")
        self.finalize_btn.setObjectName("primaryButton")
        self.finalize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.finalize_btn.clicked.connect(self._on_finalize_clicked)
        sync_row.addWidget(self.finalize_btn)

        sync_row.addStretch()
        layout.addLayout(sync_row)

        # Which file Update is currently topping up -- otherwise the only
        # way to know where the rows are going is the status line of the
        # last Update, which is gone the moment anything else is clicked.
        self.active_export_label = QLabel("")
        apply_live_style(self.active_export_label, lambda c: f"font-size: 12px; color: {c['TEXT_SECONDARY']};")
        layout.addWidget(self.active_export_label)

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
    # Cross-device sync: Update / Finalize
    # -----------------------------------------------------------------
    def _set_sync_controls_enabled(self, enabled):
        self.update_btn.setEnabled(enabled)
        self.finalize_btn.setEnabled(enabled)

    def _on_update_clicked(self):
        # No partner email is not an error: the export half of Update is
        # local work on local data and still runs. Only the mail half is
        # skipped, which the status line says (see _on_update_finished).
        email = sync_partner_settings.partner_email
        self._set_sync_controls_enabled(False)
        self.status_label.setText("Updating...")

        self._update_thread = QThread(self)
        self._update_worker = _UpdateWorker(email, project_type_settings.project_type)
        self._update_worker.moveToThread(self._update_thread)

        self._update_thread.started.connect(self._update_worker.run)
        self._update_worker.progress.connect(self.status_label.setText)
        self._update_worker.finished.connect(self._on_update_finished)
        self._update_worker.finished.connect(self._update_thread.quit)
        self._update_thread.finished.connect(self._update_thread.deleteLater)

        self._update_thread.start()

    def _on_update_finished(self, result):
        self._set_sync_controls_enabled(True)
        push = result.get("push", {})
        reason = push.get("reason")
        if push.get("sent"):
            sync_text = f"Update sent ({push.get('rows_sent', 0)} row(s)) and any incoming updates applied."
        elif reason == "no sync partner":
            sync_text = "No sync partner set - export updated locally only."
        elif reason == "sync failed":
            sync_text = "Couldn't reach Outlook to sync with the other user - export updated locally anyway."
        elif reason == "nothing to send":
            sync_text = "Up to date - nothing new to send, and any incoming updates were applied."
        else:
            sync_text = "Incoming updates applied, but sending this device's update failed - check Outlook."

        # The export half: whether the sheet was created just now or topped
        # up, and by how much -- "nothing new" being a perfectly normal
        # outcome to report, not a failure.
        export = result.get("export") or {}
        if export.get("created"):
            export_text = f" Created {export.get('path')} with {export.get('total_rows', 0)} row(s)."
        elif export.get("new_rows"):
            export_text = (
                f" Added {export['new_rows']} new row(s) to {export.get('path')} "
                f"({export.get('total_rows', 0)} in total)."
            )
        elif export:
            export_text = f" No new records for {export.get('path')}."
        else:
            export_text = ""

        self.status_label.setText(sync_text + export_text)
        self.refresh()

    def _on_finalize_clicked(self):
        # As with Update, no partner is not a blocker -- closing the sheet
        # is local. The confirmation below says plainly that nobody will
        # be told, since that IS a meaningful difference for finalizing.
        email = sync_partner_settings.partner_email

        start_str = self.from_date.date().toString("yyyy-MM-dd")
        end_str = self.to_date.date().toString("yyyy-MM-dd")
        if start_str > end_str:
            QMessageBox.warning(self, "Invalid range", "The 'from' date must be before the 'to' date.")
            return

        active_path = get_active_export_path()
        confirm = QMessageBox.question(
            self, "Finalize this period?",
            f"This closes out {start_str} to {end_str}"
            f"{' for ' + PROJECT_TYPE_LABELS[project_type_settings.project_type] if project_type_settings.project_type else ''}.\n\n"
            "One last Update runs first, then "
            + (f"{active_path} is closed as the final export"
               if active_path else "the export sheet is created and closed")
            + (f" and {email} is notified so both apps agree the period is closed."
               if email else
               ".\n\nNo sync partner is set, so nobody will be notified - the period "
               "closes on this machine only.")
            + "\n\nFrom then on, Update starts filling a NEW sheet -- this one won't "
            "be added to again.\n\n"
            "Are you sure you want to finalize?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._set_sync_controls_enabled(False)
        self.status_label.setText("Finalizing...")

        self._finalize_thread = QThread(self)
        self._finalize_worker = _FinalizeWorker(
            email, start_str, end_str, project_type_settings.project_type
        )
        self._finalize_worker.moveToThread(self._finalize_thread)

        self._finalize_thread.started.connect(self._finalize_worker.run)
        self._finalize_worker.progress.connect(self.status_label.setText)
        self._finalize_worker.finished.connect(self._on_finalize_finished)
        self._finalize_worker.finished.connect(self._finalize_thread.quit)
        self._finalize_thread.finished.connect(self._finalize_thread.deleteLater)

        self._finalize_thread.start()

    def _on_finalize_finished(self, result):
        self._set_sync_controls_enabled(True)
        row_count = result.get("row_count", 0)
        notified = result.get("notified")
        path = result.get("path", "")
        self.status_label.setText(
            f"Finalized {path} - {row_count} row(s). The next Update starts a new sheet."
        )
        if notified:
            QMessageBox.information(
                self, "Finalized",
                f"Closed {path} with {row_count} row(s) and notified the other user - the period is now "
                "closed on both apps.\n\nThe next Update will create and start filling a new sheet.",
            )
        elif not sync_partner_settings.partner_email:
            # Not a failure -- there was nobody to notify in the first
            # place, so don't dress it up as one.
            QMessageBox.information(
                self, "Finalized",
                f"Closed {path} with {row_count} row(s).\n\nNo sync partner is set, so nobody was "
                "notified - the period is closed on this machine only.\n\n"
                "The next Update will create and start filling a new sheet.",
            )
        else:
            QMessageBox.warning(
                self, "Finalized locally, but notification failed",
                f"Closed {path} with {row_count} row(s) locally, but the notification email to the other user failed to send "
                "(check that Outlook is running). The period is closed here, but their app doesn't know that yet -- "
                "you may need to resend, or have them run Update once you're able to notify them.",
            )
        self.refresh()

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
        active = get_active_export_path()
        self.active_export_label.setText(
            f"Currently filling: {active}" if active
            else "No sheet open yet - the next Update will create one."
        )

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