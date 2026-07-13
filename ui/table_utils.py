"""
Shared behavior for the record grids (Dashboard, Records): both render
whatever columns the storage layer returns (SELECT *), scroll on both
axes, and auto-fill the viewport when the columns come up short of it.
"""

from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget
from PySide6.QtCore import Qt

# "id" is the internal rowid and carries nothing for the user; the preferred
# order puts the columns people actually scan first, and anything new on the
# table shows up after them automatically rather than needing to be listed.
HIDDEN_COLUMNS = {"id", "timecard_id"}
PREFERRED_COLUMN_ORDER = [
    "status", "subject", "Project Number", "Project Name", "Task Name",
    "Date", "Qty", "rate",
]
HEADER_LABELS = {
    "status": "Status",
    "subject": "Subject",
    "sender": "Sender",
    "day": "Day",
    "period": "Period",
    "labor_type": "Labor Type",
    "time_type": "Time Type",
    "name": "Name",
    "person_number": "Person Number",
    "received": "Received",
    "rate": "Rate",
    "is_exported": "Exported",
}
MAX_COLUMN_WIDTH = 320


def order_columns(available):
    """Display order for a set of column keys: the preferred ones first,
    then everything else in the order given, minus HIDDEN_COLUMNS."""
    available = [key for key in available if key not in HIDDEN_COLUMNS]
    preferred = [key for key in PREFERRED_COLUMN_ORDER if key in available]
    return preferred + [key for key in available if key not in preferred]


def configure_grid(table: QTableWidget):
    """Scroll/resize behavior common to the record grids. Interactive
    (not Stretch) resize mode: Stretch squeezes every column into the
    viewport, so the horizontal scrollbar can never appear no matter how
    many columns the query returns. Columns are sized by fit_columns
    instead, and the user can drag them from there."""
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setStretchLastSection(False)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.verticalHeader().setVisible(False)


def set_header_labels(table: QTableWidget, columns):
    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels([HEADER_LABELS.get(c, c) for c in columns])


def fit_columns(table: QTableWidget):
    """Size each column to its content (capped, so one long Subject can't
    push the rest off-screen), then -- if the columns together fall short
    of the viewport -- grow them proportionally to fill it, so the grid is
    never left with dead space on the right. When the content is wider
    than the viewport this leaves it alone and the horizontal scrollbar
    takes over."""
    column_count = table.columnCount()
    if column_count == 0:
        return

    table.resizeColumnsToContents()
    widths = [
        min(table.columnWidth(i) + 16, MAX_COLUMN_WIDTH)
        for i in range(column_count)
    ]

    viewport_width = table.viewport().width()
    total = sum(widths)
    if total < viewport_width and total > 0:
        scale = viewport_width / total
        widths = [int(width * scale) for width in widths]
        # Integer truncation leaves a few pixels over; give them to the
        # last column so the grid lands exactly on the viewport edge.
        widths[-1] += viewport_width - sum(widths)

    for i, width in enumerate(widths):
        table.setColumnWidth(i, width)
