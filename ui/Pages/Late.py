"""
Late tab: a brief of every Pending/Rejected record that's been sitting
for at least the threshold configured in Settings (see
ui/notification_settings.py), each row saying how long it's been stuck.

Hovering a row reveals a "Send Mail" button; clicking it opens the
system's default mail client (via a mailto: link, so this works with
Outlook classic, new Outlook, Mail, Thunderbird, etc.) addressed to
that record's sender (see _send_mail_for_row).
"""

from urllib.parse import quote

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QMessageBox,
)
from PySide6.QtCore import Qt, QEvent, QUrl
from PySide6.QtGui import QColor, QBrush, QDesktopServices

from ui.theme_utils import apply_live_style
from ui.theme_manager import theme_manager
from ui.notification_settings import notification_settings
from storage_service import get_stale_records

_STATUS_COLORS = {
    "Pending": "#B58B00",
    "Rejected": "#f44336",
}

_COLUMNS = ["Status", "Subject", "Project Number", "Project Name", "Task Name", "Date", "Been like this for"]


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


_SEND_BUTTON_WIDTH = 110
_SEND_BUTTON_HEIGHT = 26


class LatePage(QWidget):
    def __init__(self):
        super().__init__()
        self.records = []       # rows currently in the table, aligned by index
        self._hover_row = None

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

        # Overlay button shown over whichever row the mouse is hovering.
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

        self.refresh()

    def refresh(self):
        threshold_hours = notification_settings.threshold_hours
        records = get_stale_records(threshold_hours)
        self.records = records
        self._hover_row = None
        self.send_button.hide()

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

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def _on_cell_entered(self, row, column):
        """Moves the Send Mail button over `row` and shows it, centered
        horizontally in the middle of the row."""
        if row < 0 or row >= len(self.records):
            self.send_button.hide()
            self._hover_row = None
            return

        self._hover_row = row
        viewport_width = self.table.viewport().width()
        x = max(0, (viewport_width - _SEND_BUTTON_WIDTH) // 2)
        y = self.table.rowViewportPosition(row) + (self.table.rowHeight(row) - _SEND_BUTTON_HEIGHT) // 2
        self.send_button.move(x, y)
        self.send_button.show()
        self.send_button.raise_()

    def eventFilter(self, obj, event):
        """Hides the Send Mail button once the mouse leaves the table
        viewport, so it doesn't linger over a row after the cursor moves
        away (cellEntered alone only fires when entering a new cell)."""
        if obj is self.table.viewport() and event.type() == QEvent.Type.Leave:
            self.send_button.hide()
            self._hover_row = None
        return super().eventFilter(obj, event)

    def _on_send_mail_clicked(self):
        if self._hover_row is None or self._hover_row >= len(self.records):
            return
        record = self.records[self._hover_row]
        self._send_mail_for_row(record)

    def _send_mail_for_row(self, record):
        """Opens a compose window in whatever mail client is registered
        as the system default, addressed to this record's sender (the
        same "sender" column stored for this timecard email in the
        database), pre-filled with a reply-style subject.

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

        if not QDesktopServices.openUrl(QUrl(mailto_url)):
            QMessageBox.critical(
                self, "Couldn't open mail client",
                "Could not open a compose window in your default mail application. "
                "Make sure a mail client is installed and set as the system default."
            )