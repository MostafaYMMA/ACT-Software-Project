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
from ui.counting_label import CountingLabel
from ui.table_utils import order_columns, configure_grid, set_header_labels, fit_columns
from ui.project_type_settings import project_type_settings
from sync_service import sync_cards
from storage_service import (
    get_status_project_counts, get_status_rows, get_status_columns,
    update_status_record_field, PROJECT_TYPE_LABELS,
    get_expense_rows, get_expense_count, get_expense_columns,
)
from date_utils import get_this_month_range, get_custom_range

CARD_ANIM_MS = 220

# Columns whose edits only make sense as numbers -- a non-numeric entry is
# rejected and the cell reverts to what the database holds.
_NUMERIC_COLUMNS = {"rate", "Qty"}

# Accent used for the QDateEdit calendar popups on this page, so they read
# as the same "orange" filter UI as the Records/History pages instead of
# Qt's default blue.
_CALENDAR_ACCENT = "#FF7A00"


def _apply_orange_calendar_style(date_edit):
    """Restyles a QDateEdit's popup QCalendarWidget (created lazily once
    setCalendarPopup(True) is set) to use the app's orange accent instead
    of Qt's default blue. Purely cosmetic -- the QDateEdit widget and all
    the .date()/.setDate()/dateChanged wiring elsewhere in this file are
    untouched, so none of the scan/filter logic changes."""
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

        # CountingLabel instead of a plain QLabel: start_loading() below
        # plays an indeterminate jitter while a scan is in progress,
        # set_value() below tweens smoothly to the real number once it's
        # known - see ui/counting_label.py.
        value_widget = CountingLabel(value)
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
        """Numeric value (int, or a numeric string) -> tween up to it via
        CountingLabel.animate_to(). Anything else (e.g. the '--' empty-state
        placeholder) -> shown as static text, no animation."""
        try:
            numeric_value = int(value)
        except (TypeError, ValueError):
            self.value_label.set_static_text(str(value))
        else:
            self.value_label.animate_to(numeric_value)

    def start_loading(self):
        """Call when a scan starts, before the real count is known."""
        self.value_label.start_spin()


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

    def __init__(self, status_key, start_date=None, end_date=None, project_type=None):
        super().__init__()
        self.status_key = status_key
        self.start_date = start_date
        self.end_date = end_date
        self.project_type = project_type

    def run(self):
        # "expenses" is a fourth pseudo-status routed to the expenses table
        # rather than one of the three timecard status tables.
        if self.status_key == "expenses":
            self.finished.emit(get_expense_rows(
                start_date=self.start_date,
                end_date=self.end_date,
                project_type=self.project_type,
            ))
            return
        self.finished.emit(get_status_rows(
            self.status_key,
            start_date=self.start_date,
            end_date=self.end_date,
            project_type=self.project_type,
        ))


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
        _apply_orange_calendar_style(self.from_date_edit)
        period_row.addWidget(self.from_date_edit)

        self.to_date_label = QLabel("To")
        apply_live_style(self.to_date_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 12px;")
        period_row.addWidget(self.to_date_label)

        self.to_date_edit = QDateEdit(today)
        self.to_date_edit.setCalendarPopup(True)
        self.to_date_edit.setMaximumDate(today)
        self.to_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.to_date_edit.setMinimumWidth(120)
        _apply_orange_calendar_style(self.to_date_edit)
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

        # Project-type row: which division's work the page is showing. Sits
        # alongside the period picker as a second filter -- both narrow the
        # SAME query, so the stat cards and the table below always describe
        # one division within one date window.
        type_row = QHBoxLayout()
        type_row.setSpacing(8)

        type_label = QLabel("Project type:")
        apply_live_style(type_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 12px;")
        type_row.addWidget(type_label)

        # None is "all types" -- the same value storage_service takes for an
        # unfiltered query, so it needs no special-casing downstream.
        type_defs = [(None, "All")] + [
            (project_type, label) for project_type, label in PROJECT_TYPE_LABELS.items()
        ]
        self._project_type_group = QButtonGroup(self)
        self._project_type_group.setExclusive(True)
        self._project_type_buttons = {}  # project_type -> button, for the sync handler below
        # Starts from whatever's already selected (possibly set on the
        # History page, or from a previous session) instead of always
        # defaulting to "All" regardless of shared state.
        self._selected_project_type = project_type_settings.project_type

        for project_type, label in type_defs:
            button = QPushButton(label)
            button.setObjectName("periodToggle")  # same pill styling as the period toggles
            button.setCheckable(True)
            button.setChecked(project_type == project_type_settings.project_type)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.toggled.connect(
                lambda checked, value=project_type: self._on_project_type_toggled(value) if checked else None
            )
            self._project_type_group.addButton(button)
            self._project_type_buttons[project_type] = button
            type_row.addWidget(button)

        # Keeps this page's buttons in lockstep with the Export History
        # page's (and vice versa) - toggling one is what fires this, on
        # both pages.
        project_type_settings.project_type_changed.connect(self._sync_project_type_selection)

        type_row.addStretch()
        layout.addLayout(type_row)

        # Stat cards - placeholder values, replaced once scan logic is wired later
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        stat_defs = [
            ("approve", "Approved Mails", "#4CAF50"),
            ("pending", "Pending Mails", "#5F5F5F"),
            ("reject", "Rejected Mails", "#f44336"),
            ("expenses", "Expenses", "#2196F3"),
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

        self.table = QTableWidget(0, 0)
        configure_grid(self.table)
        # Double-click puts the cell in edit mode; _on_item_changed writes the
        # edit back to the database (that's how rate gets set -- there's no
        # other entry point for it).
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._populating_table = False
        self._displayed_columns = []
        self._displayed_rows = []
        self._current_status_key = "approve"
        self.table.itemChanged.connect(self._on_item_changed)
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
        table_container.resizeEvent = lambda event: self._on_table_container_resized()

        self._set_empty_state()

    def _on_table_container_resized(self):
        # Re-fill on every window resize, otherwise widening the window leaves
        # a gap on the right and narrowing it hides columns that would still
        # fit if they shrank back.
        self._rows_loading_overlay.reposition()
        self._fit_columns()

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

    def _on_project_type_toggled(self, project_type):
        project_type_settings.set_project_type(project_type)

    def _sync_project_type_selection(self, project_type):
        """Fires whenever EITHER page's project-type filter changes -
        including from this page's own toggle above, in which case the
        matching button is already checked and setChecked(True) is a
        no-op (QButtonGroup won't re-fire toggled for a button that's
        already in that state)."""
        button = self._project_type_buttons.get(project_type)
        if button is not None:
            button.setChecked(True)
        self._on_project_type_changed(project_type)

    def _on_project_type_changed(self, project_type):
        self._selected_project_type = project_type
        if not getattr(self, "_has_scanned", False):
            return
        self._refresh_stat_cards()
        self._load_status_rows(getattr(self, "_selected_status_key", "approve"))

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
        for key in ("approve", "pending", "reject", "expenses"):
            if key in self.stat_cards:
                self.stat_cards[key].set_value("—")
        self.table.setRowCount(0)
        self._displayed_rows = []
        set_header_labels(self.table, self._columns_for([]))
        self._fit_columns()
        self.table_title.setText("No data yet")

    def _refresh_stat_cards(self):
        start_date, end_date = self._get_selected_period()
        counts = get_status_project_counts(
            start_date=start_date, end_date=end_date,
            project_type=self._selected_project_type,
        )
        for key in ("approve", "pending", "reject"):
            if key in self.stat_cards:
                self.stat_cards[key].set_value(counts.get(key, 0))
        # Expenses live in their own table (not the approve/pending/reject
        # split), so they're counted separately -- same window & division.
        if "expenses" in self.stat_cards:
            self.stat_cards["expenses"].set_value(get_expense_count(
                start_date=start_date, end_date=end_date,
                project_type=self._selected_project_type,
            ))

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
        self._rows_worker = RowsWorker(status_key, start_date, end_date, self._selected_project_type)
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

    def _columns_for(self, rows, status_key=None):
        """Column keys to show, in display order (see order_columns). With no
        rows to take keys from -- an empty table, before the first scan -- the
        keys come from the DB schema instead, so the headings are the real
        ones from the start. The expenses view has its own schema, so its
        empty-state headings come from the expenses table, not the timecards."""
        if rows:
            return order_columns(list(rows[0]))
        schema_columns = get_expense_columns() if status_key == "expenses" else get_status_columns()
        return order_columns(schema_columns)

    def _fit_columns(self):
        fit_columns(self.table)

    def _on_rows_loaded(self, status_key, rows):
        status_labels = {
            "approve": "Approved",
            "pending": "Pending",
            "reject": "Rejected",
            "expenses": "Expenses",
        }
        title = status_labels.get(status_key, "Approved")

        columns = self._columns_for(rows, status_key)
        self._displayed_columns = columns
        self._displayed_rows = rows
        self._current_status_key = status_key

        self._populating_table = True
        try:
            set_header_labels(self.table, columns)
            self.table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                for col_index, column in enumerate(columns):
                    value = row.get(column)
                    item = QTableWidgetItem("" if value is None else str(value))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    # Expense rows are read-only (see _on_item_changed) -- take
                    # the edit affordance off the cell so a double-click on the
                    # expenses grid doesn't open an editor that goes nowhere.
                    if status_key == "expenses":
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(row_index, col_index, item)
        finally:
            self._populating_table = False

        self._fit_columns()

        self._rows_loading_overlay.stop()
        if hasattr(self, "table_title"):
            type_label = PROJECT_TYPE_LABELS.get(self._selected_project_type)
            # "Expenses" reads better as "Expense reports" than "Expenses records".
            noun = "Expense reports" if status_key == "expenses" else f"{title} records"
            self.table_title.setText(f"{type_label} — {noun}" if type_label else noun)

        pending_key = getattr(self, "_pending_status_key", None)
        if pending_key is not None:
            self._pending_status_key = None
            self._load_status_rows(pending_key)

    def _on_item_changed(self, item):
        """Persists a double-click cell edit to the database. Fires on every
        setItem too, so the _populating_table guard filters those out. An edit
        that can't be saved -- non-numeric rate/Qty, an unknown column, or a
        DB rejection -- is rolled back in the cell so the grid never shows a
        value the database doesn't hold."""
        if self._populating_table:
            return

        # The expenses grid is read-only here -- its rows live in the expenses
        # table, not the status tables update_status_record_field writes to, so
        # persisting an edit through that path would target the wrong table.
        if self._current_status_key == "expenses":
            return

        row_index, col_index = item.row(), item.column()
        if row_index >= len(self._displayed_rows) or col_index >= len(self._displayed_columns):
            return

        record = self._displayed_rows[row_index]
        column = self._displayed_columns[col_index]
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

        if update_status_record_field(self._current_status_key, record.get("id"), column, new_value):
            record[column] = new_value
        else:
            revert()

    def scan_inbox(self):
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("Scanning...")
        self._has_scanned = False
        self._set_empty_state()

        # Play the indeterminate counting animation on all three cards
        # while the scan is in flight - _on_sync_finished below lands each
        # one on its real value once the scan completes.
        for card in self.stat_cards.values():
            card.start_loading()

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