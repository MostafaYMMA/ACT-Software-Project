from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
    QDateEdit, QButtonGroup,
)
from PySide6.QtCore import Qt, Signal, QThread, QObject, QPropertyAnimation, QEasingCurve, Property, QDate

from ui.theme_manager import theme_manager
from ui.theme_utils import apply_live_style
from ui.loading_overlay import LoadingOverlay
from sync_service import sync_cards
from storage_service import get_status_project_counts, get_status_rows
from date_utils import get_this_month_range, get_custom_range

CARD_ANIM_MS = 220


class StatCard(QFrame):
    clicked = Signal()

    def __init__(self, label, value, stripe_color):
        super().__init__()
        self._selected = False
        # Named so the selected-state QSS below can be scoped to
        # "QFrame#statCard" specifically -- an unscoped setStyleSheet()
        # call on a widget with children cascades to every descendant
        # (label_widget, value_widget, even the stripe), which is why the
        # label/value text used to render inside its own little bordered
        # box instead of just the card as a whole.
        self.setObjectName("statCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(84)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._anim_start_widths = (0, 0)
        self._anim_end_widths = (0, 0)
        self._size_progress = 1.0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        stripe = QFrame()
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(
            f"background-color: {stripe_color}; "
            f"border-top-left-radius: 6px; border-bottom-left-radius: 6px;"
        )
        layout.addWidget(stripe)

        inner = QVBoxLayout()
        inner.setContentsMargins(12, 10, 12, 10)
        inner.setSpacing(2)

        label_widget = QLabel(label)
        apply_live_style(label_widget, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 10px;")
        inner.addWidget(label_widget)

        value_widget = QLabel(value)
        apply_live_style(value_widget, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 22px; font-weight: 700;")
        inner.addWidget(value_widget)

        layout.addLayout(inner)
        self.value_label = value_widget  # exposed so callers can update it later

        # Selection styling depends on BOTH theme and selected-state, so it
        # can't use the simple apply_live_style helper (that only accounts
        # for theme). Re-run _apply_size on every theme change instead.
        theme_manager.theme_changed.connect(lambda _mode: self._apply_size())

    def set_selected(self, selected):
        self._selected = selected
        self._apply_size(animate=True)

    def _target_widths(self):
        parent = self.parentWidget()
        parent_width = max(1, parent.width()) if parent else 300
        if self._selected:
            min_width = max(140, int(parent_width * 0.28))
            max_width = max(min_width, int(parent_width * 0.38))
        else:
            min_width = max(120, int(parent_width * 0.18))
            max_width = max(min_width, int(parent_width * 0.30))
        return min_width, max_width

    # -- animatable property: drives minimumWidth AND maximumWidth from a
    # single 0..1 progress value, interpolating both from the widths
    # captured when the animation started to the new target widths. Doing
    # it this way (one property, one setter) guarantees both are always
    # updated together in the same tick -- two independent
    # QPropertyAnimations on minimumWidth/maximumWidth could drift out of
    # lockstep for a frame and leave the widget in a min > max state,
    # which is what caused the stray un-styled gap behind the card text.
    def _get_size_progress(self):
        return self._size_progress

    def _set_size_progress(self, value):
        self._size_progress = value
        start_min, start_max = self._anim_start_widths
        end_min, end_max = self._anim_end_widths
        self.setMinimumWidth(int(start_min + (end_min - start_min) * value))
        self.setMaximumWidth(int(start_max + (end_max - start_max) * value))

    size_progress = Property(float, _get_size_progress, _set_size_progress)

    def _apply_size(self, animate=False):
        parent = self.parentWidget()
        if parent is None:
            return

        min_width, max_width = self._target_widths()

        if animate:
            self._anim_start_widths = (self.minimumWidth(), self.maximumWidth())
            self._anim_end_widths = (min_width, max_width)

            anim = QPropertyAnimation(self, b"size_progress", self)
            anim.setDuration(CARD_ANIM_MS)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            self._size_anim = anim  # prevent garbage collection mid-animation
        else:
            self.setMinimumWidth(min_width)
            self.setMaximumWidth(max_width)

        colors = theme_manager.colors()
        if self._selected:
            self.setStyleSheet(
                f"QFrame#statCard {{ background-color: {colors['ACCENT_LIGHT']}; "
                f"border: 1px solid {colors['ACCENT']}; border-radius: 6px; }}"
            )
        else:
            self.setStyleSheet(f"QFrame#statCard {{ background-color: {colors['SURFACE']}; border-radius: 6px; }}")

    def resizeEvent(self, event):
        self._apply_size(animate=False)
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)

    def set_value(self, value):
        self.value_label.setText(str(value))


class SyncWorker(QObject):
    progress = Signal(str)
    finished = Signal()

    def __init__(self, start_date=None, end_date=None):
        super().__init__()
        self.start_date = start_date
        self.end_date = end_date

    def run(self):
        sync_cards(progress_callback=self.progress.emit, start_date=self.start_date, end_date=self.end_date)
        self.finished.emit()


class RowsWorker(QObject):
    """Fetches rows for one status off the GUI thread, so a slow query
    (e.g. once the DB has grown) doesn't freeze the table swap - the
    LoadingOverlay stays visible and animated for however long this
    actually takes."""
    finished = Signal(list)

    def __init__(self, status_key, start_date=None, end_date=None):
        super().__init__()
        self.status_key = status_key
        self.start_date = start_date
        self.end_date = end_date

    def run(self):
        self.finished.emit(get_status_rows(self.status_key, start_date=self.start_date, end_date=self.end_date))


class DashboardPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(14)

        # Header row: page title + scan button
        header_row = QHBoxLayout()
        title = QLabel("Dashboard")
        apply_live_style(title, lambda c: f"font-size: 18px; font-weight: 700; color: {c['TEXT_PRIMARY']};")
        header_row.addWidget(title)
        header_row.addStretch()

        self.scan_btn = QPushButton("Scan Inbox")
        self.scan_btn.setObjectName("primaryButton")
        self.scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scan_btn.clicked.connect(self.scan_inbox)
        header_row.addWidget(self.scan_btn)
        layout.addLayout(header_row)

        # Period row: choose which "received on" window the next scan
        # covers, before it runs. Two mutually exclusive modes -- This
        # Month (one click) or a custom From/To range.
        period_row = QHBoxLayout()
        period_row.setSpacing(8)

        period_label = QLabel("Scan period:")
        apply_live_style(period_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 12px;")
        period_row.addWidget(period_label)

        self.this_month_btn = QPushButton("This Month")
        self.this_month_btn.setObjectName("periodToggle")
        self.this_month_btn.setCheckable(True)
        self.this_month_btn.setChecked(True)
        self.this_month_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        period_row.addWidget(self.this_month_btn)

        self.custom_range_btn = QPushButton("Custom Range")
        self.custom_range_btn.setObjectName("periodToggle")
        self.custom_range_btn.setCheckable(True)
        self.custom_range_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        period_row.addWidget(self.custom_range_btn)

        self._period_group = QButtonGroup(self)
        self._period_group.setExclusive(True)
        self._period_group.addButton(self.this_month_btn)
        self._period_group.addButton(self.custom_range_btn)

        today = QDate.currentDate()
        month_start = QDate(today.year(), today.month(), 1)

        self.from_date_label = QLabel("From")
        apply_live_style(self.from_date_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 12px;")
        period_row.addWidget(self.from_date_label)

        self.from_date_edit = QDateEdit(month_start)
        self.from_date_edit.setCalendarPopup(True)
        self.from_date_edit.setMaximumDate(today)
        self.from_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.from_date_edit.setMinimumWidth(120)
        period_row.addWidget(self.from_date_edit)

        self.to_date_label = QLabel("To")
        apply_live_style(self.to_date_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 12px;")
        period_row.addWidget(self.to_date_label)

        self.to_date_edit = QDateEdit(today)
        self.to_date_edit.setCalendarPopup(True)
        self.to_date_edit.setMaximumDate(today)
        self.to_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.to_date_edit.setMinimumWidth(120)
        period_row.addWidget(self.to_date_edit)

        # Keep from <= to at all times, in either direction of edit.
        self.from_date_edit.dateChanged.connect(self._on_from_date_changed)
        self.to_date_edit.dateChanged.connect(self._on_to_date_changed)

        self.custom_range_btn.toggled.connect(self._update_period_controls_enabled)
        self._update_period_controls_enabled()

        # The period picker doubles as a live filter on whatever's already
        # in the DB, not just a gate on the next scan -- changing it
        # re-queries immediately so the stat cards/table always reflect
        # the currently selected window.
        self.this_month_btn.toggled.connect(self._on_period_changed)
        self.custom_range_btn.toggled.connect(self._on_period_changed)
        self.from_date_edit.dateChanged.connect(self._on_period_changed)
        self.to_date_edit.dateChanged.connect(self._on_period_changed)

        period_row.addStretch()
        layout.addLayout(period_row)

        # Stat cards - placeholder values, replaced once scan logic is wired later
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        stat_defs = [
            ("approve", "Approved Mails", "#4CAF50"),
            ("pending", "Pending Mails", "#5F5F5F"),
            ("reject", "Rejected Mails", "#f44336"),
        ]
        self.stat_cards = {}
        for key, label, stripe_color in stat_defs:
            card = StatCard(label, "0", stripe_color)
            card.clicked.connect(lambda checked=False, card_key=key: self.select_stat_card(card_key))
            self.stat_cards[key] = card
            stats_row.addWidget(card)
        layout.addLayout(stats_row)

        # Matching subjects table
        # NOTE: only "Subject" for now - the scan only reads the subject
        # line, not the email body, so there's no consultant/project/period
        # data to show per row yet. That needs body-parsing logic first.
        self.table_title = QLabel("Matching subjects")
        apply_live_style(self.table_title, lambda c: f"font-size: 13px; font-weight: 700; color: {c['TEXT_PRIMARY']};")
        layout.addWidget(self.table_title)

        table_container = QWidget()
        table_container_layout = QVBoxLayout(table_container)
        table_container_layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Subject", "Project Number", "Project Name", "Task Name", "Date", "Qty"])
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
        table_container_layout.addWidget(self.table)
        layout.addWidget(table_container, stretch=1)

        self._rows_loading_overlay = LoadingOverlay(table_container, message="Loading records...")
        table_container.resizeEvent = lambda event: self._rows_loading_overlay.reposition()

        self._set_empty_state()

    def _on_from_date_changed(self, qdate):
        if qdate > self.to_date_edit.date():
            self.to_date_edit.setDate(qdate)

    def _on_to_date_changed(self, qdate):
        if qdate < self.from_date_edit.date():
            self.from_date_edit.setDate(qdate)

    def _update_period_controls_enabled(self):
        is_custom = self.custom_range_btn.isChecked()
        for widget in (self.from_date_label, self.from_date_edit, self.to_date_label, self.to_date_edit):
            widget.setEnabled(is_custom)

    def _get_selected_period(self):
        """Returns (start_date, end_date) datetimes for the currently
        chosen scan period, per date_utils' rules."""
        if self.custom_range_btn.isChecked():
            from_qdate = self.from_date_edit.date()
            to_qdate = self.to_date_edit.date()
            start = datetime(from_qdate.year(), from_qdate.month(), from_qdate.day())
            end = datetime(to_qdate.year(), to_qdate.month(), to_qdate.day())
            return get_custom_range(start, end)
        return get_this_month_range()

    def select_stat_card(self, selected_key):
        self._selected_status_key = selected_key
        for key, card in self.stat_cards.items():
            card.set_selected(key == selected_key)
        if hasattr(self, "table"):
            self._load_status_rows(selected_key)

    def _on_period_changed(self, *_args):
        # Fires while the widgets are still being constructed (initial
        # setChecked calls, etc.) and on every keystroke/clamp of the date
        # editors -- only act once there's actually data on screen to
        # refilter.
        if not getattr(self, "_has_scanned", False):
            return
        self._refresh_stat_cards()
        selected_key = getattr(self, "_selected_status_key", None)
        if selected_key is not None:
            self._load_status_rows(selected_key)

    def _is_rows_loading(self):
        thread = getattr(self, "_rows_thread", None)
        if thread is None:
            return False
        try:
            return thread.isRunning()
        except RuntimeError:
            # The underlying C++ QThread was already deleted (deleteLater
            # ran before this Python reference got cleared) -- treat that
            # the same as "not loading" instead of crashing.
            self._rows_thread = None
            return False

    def _set_empty_state(self):
        for key in ("approve", "pending", "reject"):
            if key in self.stat_cards:
                self.stat_cards[key].set_value("—")
        self.table.setRowCount(0)
        self.table_title.setText("No data yet")

    def _refresh_stat_cards(self):
        start_date, end_date = self._get_selected_period()
        counts = get_status_project_counts(start_date=start_date, end_date=end_date)
        for key in ("approve", "pending", "reject"):
            if key in self.stat_cards:
                self.stat_cards[key].set_value(counts.get(key, 0))

    def _load_status_rows(self, status_key):
        if not getattr(self, "_has_scanned", False):
            self.table.setRowCount(0)
            self.table_title.setText("No data yet")
            return

        if self._is_rows_loading():
            # A fetch for a previous selection is still in flight -- just
            # remember the latest request; _on_rows_loaded kicks it off
            # once the in-flight one finishes, so requests never overlap
            # (mirrors scan_inbox disabling its button while it runs).
            self._pending_status_key = status_key
            return

        self.table.setRowCount(0)
        self._rows_loading_overlay.start("Loading records...")

        start_date, end_date = self._get_selected_period()
        self._rows_thread = QThread(self)
        self._rows_worker = RowsWorker(status_key, start_date, end_date)
        self._rows_worker.moveToThread(self._rows_thread)

        self._rows_thread.started.connect(self._rows_worker.run)
        self._rows_worker.finished.connect(lambda rows: self._on_rows_loaded(status_key, rows))
        self._rows_worker.finished.connect(self._rows_thread.quit)
        self._rows_thread.finished.connect(self._rows_thread.deleteLater)
        self._rows_thread.finished.connect(self._clear_rows_thread_ref)

        self._rows_thread.start()

    def _clear_rows_thread_ref(self):
        # Runs synchronously on the "finished" signal, before deleteLater's
        # deferred deletion actually destroys the C++ object -- so this
        # always beats the RuntimeError _is_rows_loading() guards against.
        self._rows_thread = None

    def _on_rows_loaded(self, status_key, rows):
        status_labels = {
            "approve": "Approved",
            "pending": "Pending",
            "reject": "Rejected",
        }
        title = status_labels.get(status_key, "Approved")

        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.get("subject") or "",
                row.get("Project Number") or "",
                row.get("Project Name") or "",
                row.get("Task Name") or "",
                row.get("Date") or "",
                row.get("Qty") or "",
            ]
            for col_index, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_index, col_index, item)

        self._rows_loading_overlay.stop()
        if hasattr(self, "table_title"):
            self.table_title.setText(f"{title} records")

        pending_key = getattr(self, "_pending_status_key", None)
        if pending_key is not None:
            self._pending_status_key = None
            self._load_status_rows(pending_key)

    def scan_inbox(self):
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("Scanning...")
        self._has_scanned = False
        self._set_empty_state()

        start_date, end_date = self._get_selected_period()

        self._sync_thread = QThread(self)
        self._sync_worker = SyncWorker(start_date, end_date)
        self._sync_worker.moveToThread(self._sync_thread)

        self._sync_thread.started.connect(self._sync_worker.run)
        self._sync_worker.progress.connect(self._on_sync_progress)
        self._sync_worker.finished.connect(self._on_sync_finished)
        self._sync_worker.finished.connect(self._sync_thread.quit)
        self._sync_thread.finished.connect(self._sync_thread.deleteLater)

        self._sync_thread.start()

    def _on_sync_progress(self, message):
        print(message)

    def _on_sync_finished(self):
        self._has_scanned = True
        self._refresh_stat_cards()
        self.select_stat_card("approve")
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Scan Inbox")