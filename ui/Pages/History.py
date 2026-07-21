from datetime import date, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QDateEdit, QFileDialog, QMessageBox, QButtonGroup,
)
from PySide6.QtCore import Qt, QDate, QThread, QObject, Signal, QSettings

from ui.theme_utils import apply_live_style
from ui.project_type_settings import project_type_settings
from ui.sync_partner_settings import sync_partner_settings
from ui.profile_circle import SETTINGS_ORG, SETTINGS_APP
from storage_service import (
    get_export_history, export_act_invoice_overview_range, get_last_export_date,
    PROJECT_TYPE_LABELS,
)
from sync_service import update_with_other_user, finalize_month, local_update, local_finalize

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

# Same key ui/Pages/Settings.py's Sync switch writes to -- when False,
# Update/Finalize skip the partner-email requirement entirely and run
# local-only (see sync_service.local_update / local_finalize).
SYNC_ENABLED_KEY = "sync_enabled"


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
    of Qt's default blue. Purely cosmetic -- the QDateEdit/QCalendarWidget
    widgets and all the .date()/.setDate() calls elsewhere in this file
    are untouched, so none of the export/finalize logic changes."""
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


class _LocalUpdateWorker(QObject):
    """Sync-off equivalent of _UpdateWorker: no recipient email, no
    pull/push, just a local inbox scan (sync_service.local_update)."""
    progress = Signal(str)
    finished = Signal(dict)

    def run(self):
        result = local_update(progress_callback=self.progress.emit)
        self.finished.emit(result)


class _FinalizeWorker(QObject):
    progress = Signal(str)
    finished = Signal(dict)

    def __init__(self, recipient_email, start_date, end_date, output_path, project_type):
        super().__init__()
        self.recipient_email = recipient_email
        self.start_date = start_date
        self.end_date = end_date
        self.output_path = output_path
        self.project_type = project_type

    def run(self):
        result = finalize_month(
            self.recipient_email, self.start_date, self.end_date, self.output_path,
            project_type=self.project_type, progress_callback=self.progress.emit,
        )
        self.finished.emit(result)


class _LocalFinalizeWorker(QObject):
    """Sync-off equivalent of _FinalizeWorker: no recipient email, no
    pull/notify, just a local scan + export (sync_service.local_finalize)."""
    progress = Signal(str)
    finished = Signal(dict)

    def __init__(self, start_date, end_date, output_path, project_type):
        super().__init__()
        self.start_date = start_date
        self.end_date = end_date
        self.output_path = output_path
        self.project_type = project_type

    def run(self):
        result = local_finalize(
            self.start_date, self.end_date, self.output_path,
            project_type=self.project_type, progress_callback=self.progress.emit,
        )
        self.finished.emit(result)


class HistoryPage(QWidget):
    """Export controls (last month / custom date range, both filtering
    by the date each email was RECEIVED - see storage_service._to_row)
    plus a log of every export ever produced (name + date), newest first."""

    def __init__(self):
        super().__init__()
        self._settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

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
        # Update: pulls in anything the other user has sent, then pushes
        # this device's own new scans out to them -- both apps converge on
        # the same live picture of the current (not-yet-finalized) period.
        # Finalize: one last update pass, then exports the real file and
        # notifies the other user, which is what actually closes the
        # period on BOTH machines (see services/sync_service.py).
        # Both buttons check _sync_enabled() (the Settings page's Sync
        # switch) first -- when sync is off, they skip the partner-email
        # requirement and run local-only (see _LocalUpdateWorker /
        # _LocalFinalizeWorker above).
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
    # Sync on/off (Settings page switch)
    # -----------------------------------------------------------------
    def _sync_enabled(self):
        return self._settings.value(SYNC_ENABLED_KEY, True, type=bool)

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
    def _require_partner_email(self):
        email = sync_partner_settings.partner_email
        if not email:
            QMessageBox.warning(
                self, "No sync partner set",
                "Set the other user's email address in Settings first "
                "(Settings -> Sync) before using Update or Finalize.",
            )
            return None
        return email

    def _set_sync_controls_enabled(self, enabled):
        self.update_btn.setEnabled(enabled)
        self.finalize_btn.setEnabled(enabled)

    def _on_update_clicked(self):
        if self._sync_enabled():
            email = self._require_partner_email()
            if not email:
                return

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
        else:
            # Sync is off -- no partner email needed, just scan this
            # device's own inbox.
            self._set_sync_controls_enabled(False)
            self.status_label.setText("Updating (sync is off - local scan only)...")

            self._update_thread = QThread(self)
            self._update_worker = _LocalUpdateWorker()
            self._update_worker.moveToThread(self._update_thread)

            self._update_thread.started.connect(self._update_worker.run)
            self._update_worker.progress.connect(self.status_label.setText)
            self._update_worker.finished.connect(self._on_local_update_finished)
            self._update_worker.finished.connect(self._update_thread.quit)
            self._update_thread.finished.connect(self._update_thread.deleteLater)

            self._update_thread.start()

    def _on_update_finished(self, result):
        self._set_sync_controls_enabled(True)
        push = result.get("push", {})
        if push.get("sent"):
            self.status_label.setText(f"Update sent ({push.get('rows_sent', 0)} row(s)) and any incoming updates applied.")
        elif push.get("reason") == "nothing to send":
            self.status_label.setText("Up to date - nothing new to send, and any incoming updates were applied.")
        else:
            self.status_label.setText("Incoming updates applied, but sending this device's update failed - check Outlook.")
        self.refresh()

    def _on_local_update_finished(self, _result):
        self._set_sync_controls_enabled(True)
        self.status_label.setText("Inbox scanned locally. Sync is off, so nothing was sent to another user.")
        self.refresh()

    def _on_finalize_clicked(self):
        start_str = self.from_date.date().toString("yyyy-MM-dd")
        end_str = self.to_date.date().toString("yyyy-MM-dd")
        if start_str > end_str:
            QMessageBox.warning(self, "Invalid range", "The 'from' date must be before the 'to' date.")
            return

        sync_on = self._sync_enabled()
        email = None
        if sync_on:
            email = self._require_partner_email()
            if not email:
                return

        scope_text = (
            " for " + PROJECT_TYPE_LABELS[project_type_settings.project_type]
            if project_type_settings.project_type else ""
        )
        message = f"This exports and closes out {start_str} to {end_str}{scope_text}.\n\n"
        if sync_on:
            message += (
                "One last check for updates runs first, then the sheet is exported and "
                f"{email} is notified so both apps agree the period is closed.\n\n"
            )
        else:
            message += (
                "Sync is currently off, so this closes out the period locally only - "
                "no other user will be notified.\n\n"
            )
        message += "Are you sure you want to finalize?"

        confirm = QMessageBox.question(
            self, "Finalize this period?", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save final export as", self._default_name(start_str, end_str), "Excel files (*.xlsx)"
        )
        if not path:
            return  # user cancelled the save dialog -- nothing has happened yet, safe to just stop here

        self._set_sync_controls_enabled(False)
        self.status_label.setText("Finalizing...")

        self._finalize_thread = QThread(self)
        if sync_on:
            self._finalize_worker = _FinalizeWorker(
                email, start_str, end_str, path, project_type_settings.project_type
            )
        else:
            self._finalize_worker = _LocalFinalizeWorker(
                start_str, end_str, path, project_type_settings.project_type
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
        self.status_label.setText(f"Finalized - {row_count} row(s) exported.")
        if notified is None:
            # Sync was off -- nothing was ever attempted, so this isn't a
            # failure, just the expected local-only outcome.
            QMessageBox.information(
                self, "Finalized",
                f"Exported {row_count} row(s) locally. Sync is off, so no other user was notified.",
            )
        elif notified:
            QMessageBox.information(
                self, "Finalized",
                f"Exported {row_count} row(s) and notified the other user - the period is now closed on both apps.",
            )
        else:
            QMessageBox.warning(
                self, "Finalized locally, but notification failed",
                f"Exported {row_count} row(s) locally, but the notification email to the other user failed to send "
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