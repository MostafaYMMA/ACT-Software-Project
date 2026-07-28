"""
Read-only "what has changed on this row" dialog, opened from the Current
Sheet's row right-click menu (see ui/Pages/CurrentSheet.py's
_on_context_menu).

Strictly an audit trail: it only ever READS storage_service's change_log
(get_change_log_for_row) and offers no way to edit, revert or delete an
entry. It also has no bearing on how sync resolves an overwrite -- that
stays last-write-wins exactly as before; this just explains, after the
fact, what a given value used to be and who replaced it.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
)
from PySide6.QtCore import Qt

import re

from ui.theme_utils import apply_live_style
from ui.table_utils import HEADER_LABELS
from ui.color_names import closest_color_name
from storage_service import get_change_log_for_row

# "old -> new" is shown with an actual arrow column rather than one
# "100 -> 250" string, so a value that itself contains "->" can't be
# misread as the separator.
_COLUMNS = ["When", "Field", "From", "To", "Changed by"]

_EMPTY_VALUE_TEXT = "(empty)"

# Fields HEADER_LABELS doesn't cover, because they're hidden columns in
# every grid that uses it -- they still need a readable name here, since
# this view is the one place they're ever shown to the user by name.
_EXTRA_FIELD_LABELS = {
    "row_color": "Row colour",
}

# Which change_log.field_name values hold a hex colour rather than plain
# text/numbers -- only these get run through closest_color_name below. A
# rate or Qty edit must keep showing its literal From/To values untouched.
_COLOR_FIELDS = {"row_color"}

# A synced entry's source is the sending device's id (see
# storage_service.apply_incoming_snapshot / _apply_rate_if_newer /
# _apply_color_if_newer, none of which changed here), always this exact
# shape -- see storage_service.get_device_id. A local edit's source is now
# the real logged-in username (see ui/Pages/CurrentSheet.py), which won't
# incidentally take this shape in ordinary use, so it's what distinguishes
# "another device" from "a person's name" in the one shared text column.
_DEVICE_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")


def _display_field(field):
    return _EXTRA_FIELD_LABELS.get(field, HEADER_LABELS.get(field, field))


def _display_value(value, is_color=False):
    """NULL/blank shown as an explicit placeholder -- an empty cell next to
    another empty cell would otherwise read as "nothing happened", when
    clearing a value is itself a change worth showing.

    is_color routes a real hex value through closest_color_name so a
    row_color entry reads as "Medium Orchid" instead of "#9B59B6" -- display
    only, the stored value itself is never touched. A colour with no
    reasonably close named match (see color_names.DEFAULT_MAX_DISTANCE)
    falls back to the raw hex rather than being mislabeled.
    """
    if value is None or str(value) == "":
        return _EMPTY_VALUE_TEXT
    if is_color:
        name = closest_color_name(value)
        if name:
            return name
    return str(value)


def _display_source(source):
    """A synced entry's source is a device id -- shown with a label, since
    a bare hex string is a useless identity on its own. A local edit's
    source is now the real logged-in username, shown as-is."""
    if not source:
        return "Unknown"
    if _DEVICE_ID_PATTERN.match(source):
        return f"Other device ({source})"
    return source


class ChangeLogDialog(QDialog):
    def __init__(self, timecard_id, row_description="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Change history")
        self.setMinimumSize(620, 340)
        apply_live_style(self, lambda c: f"ChangeLogDialog {{ background-color: {c['BG']}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("Change history")
        apply_live_style(title, lambda c: (
            f"font-size: 16px; font-weight: 700; color: {c['TEXT_PRIMARY']};"
            f" background-color: transparent;"
        ))
        layout.addWidget(title)

        if row_description:
            subtitle = QLabel(row_description)
            subtitle.setWordWrap(True)
            apply_live_style(subtitle, lambda c: (
                f"font-size: 12px; color: {c['TEXT_SECONDARY']};"
                f" background-color: transparent;"
            ))
            layout.addWidget(subtitle)

        entries = get_change_log_for_row(timecard_id)

        self.table = QTableWidget(len(entries), len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        # Read-only in every sense the widget offers: no editing, and no
        # per-cell selection that would imply one.
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(False)
        apply_live_style(self.table, lambda c: f"""
            QTableWidget {{
                border: 1px solid {c['BORDER']}; background: {c['BG']};
                color: {c['TEXT_PRIMARY']}; gridline-color: {c['BORDER']};
            }}
            QTableWidget::item {{ color: {c['TEXT_PRIMARY']}; padding: 4px; }}
            QHeaderView::section {{
                background-color: {c['SURFACE']}; color: {c['TEXT_PRIMARY']};
                padding: 6px; border: none; font-weight: 700;
            }}
        """)

        for row_index, entry in enumerate(entries):
            field = entry.get("field_name") or ""
            is_color = field in _COLOR_FIELDS
            values = [
                entry.get("changed_at") or "",
                _display_field(field),
                _display_value(entry.get("old_value"), is_color=is_color),
                _display_value(entry.get("new_value"), is_color=is_color),
                _display_source(entry.get("source")),
            ]
            for col_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row_index, col_index, item)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, stretch=1)

        if not entries:
            empty = QLabel("No changes recorded for this row yet.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            apply_live_style(empty, lambda c: (
                f"font-size: 12px; color: {c['TEXT_SECONDARY']};"
                f" background-color: transparent;"
            ))
            layout.addWidget(empty)

        button_row = QHBoxLayout()
        button_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondaryButton")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)
