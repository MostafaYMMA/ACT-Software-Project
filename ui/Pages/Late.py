"""
Late tab: a brief of every Pending/Rejected record that's been sitting
for at least the threshold configured in Settings (see
ui/notification_settings.py), each row saying how long it's been stuck.

Hovering a row expands that row's own height downward, opening a gap
below its normal content just tall enough for a "Send Mail" button,
centered across the full row width. Clicking it opens the system's
default mail client (via a mailto: link, so this works with Outlook
classic, new Outlook, Mail, Thunderbird, etc.) addressed to that record's
sender, optionally pre-filled with a preset body configured in Settings
(see ui/late_mail_body_settings.py) -- see _send_mail_for_row.
"""

from urllib.parse import quote

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QMessageBox,
)
from PySide6.QtCore import Qt, QEvent, QUrl, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QBrush, QDesktopServices, QCursor

from ui.theme_utils import apply_live_style
from ui.theme_manager import theme_manager
from ui.notification_settings import notification_settings
from ui.late_mail_body_settings import late_mail_body_settings
from storage_service import get_stale_records

_STATUS_COLORS = {
    "Pending": "#B58B00",
    "Rejected": "#f44336",
}

_COLUMNS = ["Status", "Subject", "Project Number", "Project Name", "Task Name", "Date", "Been like this for"]

_SEND_BUTTON_WIDTH = 110
_SEND_BUTTON_HEIGHT = 26

# How long the hover expand/collapse animation takes, and how much extra
# room (above the button's own height) is left as breathing room inside
# the opened gap. Named constants since these are the two knobs most
# likely to need retuning by feel.
_EXPAND_ANIMATION_MS = 150
_GAP_PADDING = 10


def _format_age(hours):
    if hours < 24:
        h = int(hours)
        return f"{h} hour{'s' if h != 1 else ''}"
    days = hours / 24
    if days < 7:
        d = int(days)
        return f"{d} day{'s' if d != 1 else ''}"
    weeks = int(days // 7)
    return f"{weeks} week{'s' if weeks != 1 else ''}"


def _format_threshold(hours):
    if hours >= 24 and hours % 24 == 0:
        days = int(hours // 24)
        return f"{days} day{'s' if days != 1 else ''}"
    return f"{int(hours)} hour{'s' if int(hours) != 1 else ''}"


class LatePage(QWidget):
    def __init__(self):
        super().__init__()
        self.records = []       # rows currently in the table, aligned by index
        self._expanded_row = None    # row currently expanded (open gap + visible button), or None
        self._collapsing_row = None  # row currently animating back to normal height, or None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(14)

        title = QLabel("Late")
        apply_live_style(title, lambda c: f"font-size: 18px; font-weight: 700; color: {c['TEXT_PRIMARY']};")
        layout.addWidget(title)

        self.subtitle = QLabel("")
        apply_live_style(self.subtitle, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 11px;")
        layout.addWidget(self.subtitle)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
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

        # Qt's own default row height -- the height every row returns to
        # once collapsed, and the baseline the expanded height is computed
        # from. Read once up front rather than off row 0, since row 0 may
        # not exist yet (empty table) or may itself be mid-animation later.
        self._normal_row_height = self.table.verticalHeader().defaultSectionSize()

        # Reusable animation objects (not recreated per hover) so rapid
        # cursor movement across many rows can never leave orphaned
        # animations running -- each new hover just restarts these.
        self._expand_anim = QVariantAnimation(self)
        self._expand_anim.setDuration(_EXPAND_ANIMATION_MS)
        self._expand_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._expand_anim.valueChanged.connect(self._on_expand_value_changed)

        self._collapse_anim = QVariantAnimation(self)
        self._collapse_anim.setDuration(_EXPAND_ANIMATION_MS)
        self._collapse_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._collapse_anim.valueChanged.connect(self._on_collapse_value_changed)
        self._collapse_anim.finished.connect(self._on_collapse_finished)

        # Overlay button shown over whichever row is currently expanded.
        # Parented to the viewport so it scrolls with the rows and sits on
        # top of the cell items.
        self.send_button = QPushButton("Send Mail", self.table.viewport())
        self.send_button.setFixedSize(_SEND_BUTTON_WIDTH, _SEND_BUTTON_HEIGHT)
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_live_style(self.send_button, lambda c: f"""
            QPushButton {{
                background-color: {c['ACCENT']}; color: {c['TEXT_ON_ACCENT']};
                border: none; border-radius: 4px; font-weight: 600; font-size: 11px;
            }}
            QPushButton:hover {{ background-color: {c['ACCENT_DARK']}; }}
        """)
        self.send_button.hide()
        self.send_button.clicked.connect(self._on_send_mail_clicked)

        self.table.setMouseTracking(True)
        self.table.viewport().setMouseTracking(True)
        self.table.cellEntered.connect(self._on_cell_entered)
        self.table.viewport().installEventFilter(self)
        self.table.verticalScrollBar().valueChanged.connect(self._reposition_button_for_expanded_row)
        self.table.horizontalScrollBar().valueChanged.connect(self._reposition_button_for_expanded_row)

        self.refresh()

    def refresh(self):
        # A rebuilt table must never carry over an expanded row from
        # before -- stop anything in flight and reset state up front.
        self._expand_anim.stop()
        self._collapse_anim.stop()
        self._expanded_row = None
        self._collapsing_row = None
        self.send_button.hide()

        threshold_hours = notification_settings.threshold_hours
        records = get_stale_records(threshold_hours)
        self.records = records

        self.subtitle.setText(
            f"Pending or rejected for at least {_format_threshold(threshold_hours)} "
            f"({len(records)} record{'s' if len(records) != 1 else ''}) -- "
            f"change the threshold in Settings."
        )

        self.table.setRowCount(len(records))
        for row_index, record in enumerate(records):
            status = record.get("status", "")
            values = [
                status,
                record.get("subject") or "",
                record.get("Project Number") or "",
                record.get("Project Name") or "",
                record.get("Task Name") or "",
                record.get("Date") or "",
                _format_age(record.get("age_hours", 0)),
            ]
            for col_index, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col_index == 0:
                    color = _STATUS_COLORS.get(status)
                    if color:
                        item.setForeground(Qt.GlobalColor.white)
                        item.setBackground(QBrush(QColor(color)))
                self.table.setItem(row_index, col_index, item)

            self.table.setRowHeight(row_index, self._normal_row_height)

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def _on_cell_entered(self, row, column):
        """Expands `row` (opening the button gap) and collapses whatever
        row was previously expanded. A no-op if `row` is already the
        expanded row -- notably, this is also what keeps the row expanded
        while the cursor moves DOWN into the newly opened gap: since the
        row's own height now covers that gap, the item cells still span
        it, so cellEntered keeps reporting the same row rather than
        signalling a leave."""
        if row < 0 or row >= len(self.records):
            self._collapse_expanded_row()
            return
        if row == self._expanded_row:
            return
        self._expand_row(row)

    def _expand_row(self, row):
        previous = self._expanded_row
        self._expand_anim.stop()
        self._collapse_anim.stop()

        if previous is not None and previous != row and previous < self.table.rowCount():
            self._collapsing_row = previous
            self._collapse_anim.setStartValue(float(self.table.rowHeight(previous)))
            self._collapse_anim.setEndValue(float(self._normal_row_height))
            self._collapse_anim.start()
        else:
            self._collapsing_row = None

        self._expanded_row = row
        expanded_height = self._normal_row_height + _SEND_BUTTON_HEIGHT + 2 * _GAP_PADDING
        self._expand_anim.setStartValue(float(self.table.rowHeight(row)))
        self._expand_anim.setEndValue(float(expanded_height))
        self.send_button.show()
        self.send_button.raise_()
        self._expand_anim.start()

    def _collapse_expanded_row(self):
        """Collapses whatever row is currently expanded (if any) and hides
        the button -- used when the cursor truly leaves the table."""
        if self._expanded_row is None:
            return
        row = self._expanded_row
        self._expanded_row = None
        self.send_button.hide()

        self._expand_anim.stop()
        self._collapse_anim.stop()
        if row < self.table.rowCount():
            self._collapsing_row = row
            self._collapse_anim.setStartValue(float(self.table.rowHeight(row)))
            self._collapse_anim.setEndValue(float(self._normal_row_height))
            self._collapse_anim.start()

    def _on_expand_value_changed(self, value):
        row = self._expanded_row
        if row is None or row >= self.table.rowCount():
            return
        self.table.setRowHeight(row, int(value))
        self._position_button_over_row(row, float(value))

    def _on_collapse_value_changed(self, value):
        row = self._collapsing_row
        if row is None or row >= self.table.rowCount():
            return
        self.table.setRowHeight(row, int(value))

    def _on_collapse_finished(self):
        row = self._collapsing_row
        self._collapsing_row = None
        if row is not None and row < self.table.rowCount():
            self.table.setRowHeight(row, self._normal_row_height)

    def _position_button_over_row(self, row, current_height):
        """Centers the button horizontally across the full table width,
        vertically centered within whatever extra gap the row currently
        has open below its normal content (grows smoothly as the
        animation progresses, rather than only appearing once finished)."""
        extra = max(0.0, current_height - self._normal_row_height)
        row_top = self.table.rowViewportPosition(row)
        y = row_top + self._normal_row_height + max(0.0, (extra - _SEND_BUTTON_HEIGHT) / 2)
        viewport_width = self.table.viewport().width()
        x = max(0, (viewport_width - _SEND_BUTTON_WIDTH) // 2)
        self.send_button.move(int(x), int(y))

    def _reposition_button_for_expanded_row(self, *_args):
        """Keeps the button glued to its row when the table is scrolled --
        hides it instead of leaving it floating over the wrong row if the
        expanded row scrolls out of the visible viewport entirely."""
        if self._expanded_row is None or self._expanded_row >= self.table.rowCount():
            return
        row = self._expanded_row
        row_height = self.table.rowHeight(row)
        row_top = self.table.rowViewportPosition(row)
        viewport_height = self.table.viewport().height()
        if row_top + row_height <= 0 or row_top >= viewport_height:
            self.send_button.hide()
            return
        self.send_button.show()
        self._position_button_over_row(row, float(row_height))

    def eventFilter(self, obj, event):
        """Collapses the expanded row once the mouse truly leaves the
        table viewport. A plain QEvent.Leave isn't enough on its own: Qt
        also delivers Leave to the viewport when the cursor moves onto the
        Send Mail button (a child widget occupying part of the expanded
        row's gap), even though the cursor is still visually within the
        viewport's bounds -- collapsing on that would hide the button out
        from under a cursor that's trying to click it. Checking the
        cursor's actual position against the viewport's rect distinguishes
        "moved onto the button" (still inside) from "actually left"."""
        if obj is self.table.viewport() and event.type() == QEvent.Type.Leave:
            pos = self.table.viewport().mapFromGlobal(QCursor.pos())
            if not self.table.viewport().rect().contains(pos):
                self._collapse_expanded_row()
        return super().eventFilter(obj, event)

    def _on_send_mail_clicked(self):
        if self._expanded_row is None or self._expanded_row >= len(self.records):
            return
        record = self.records[self._expanded_row]
        self._send_mail_for_row(record)

    def _send_mail_for_row(self, record):
        """Opens a compose window in whatever mail client is registered
        as the system default, addressed to this record's sender (the
        same "sender" column stored for this timecard email in the
        database), pre-filled with a reply-style subject and, if one is
        configured in Settings (see ui/late_mail_body_settings.py), a
        preset body -- used exactly as typed, no per-record templating.

        Uses a mailto: link handed off to QDesktopServices rather than
        driving Outlook via COM, so it works with Outlook classic, new
        Outlook, Mail, Thunderbird, or anything else registered as the
        default mail handler. As with the old approach, this only opens
        a compose window -- the user reviews/edits and sends it
        themselves.
        """
        sender = (record.get("sender") or "").strip()
        if not sender:
            QMessageBox.warning(
                self, "No sender on file",
                "This record doesn't have a sender email address stored, so a mail can't be composed."
            )
            return

        subject = record.get("subject") or ""
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}" if subject else ""

        mailto_url = f"mailto:{quote(sender)}?subject={quote(reply_subject)}"

        preset_body = late_mail_body_settings.preset_body
        if preset_body:
            mailto_url += f"&body={quote(preset_body)}"

        if not QDesktopServices.openUrl(QUrl(mailto_url)):
            QMessageBox.critical(
                self, "Couldn't open mail client",
                "Could not open a compose window in your default mail application. "
                "Make sure a mail client is installed and set as the system default."
            )
