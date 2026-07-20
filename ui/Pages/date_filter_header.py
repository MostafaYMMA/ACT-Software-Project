"""
Custom QHeaderView that draws a small clickable filter arrow on specific
header sections (e.g. "Date") without touching QTableWidget/QHeaderView's
built-in click/resize behavior for every other column. Clicking the arrow
emits a signal with the section's logical index and a screen position
suitable for anchoring a popup underneath it. Everything else about the
header (labels, resizing, etc.) is left to the base QHeaderView / whatever
table_utils.configure_grid already sets up -- this file doesn't touch
table_utils.py at all.
"""

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHeaderView

from ui.theme_manager import theme_manager

_ICON_SIZE = 9
_ICON_MARGIN = 10


class FilterableHeaderView(QHeaderView):
    """A QHeaderView that paints a small downward filter arrow on the
    sections listed in `filterable_columns` (matched by their *label*,
    since QTableWidget columns can be rebuilt by
    table_utils.set_header_labels at any time -- matching on label rather
    than a fixed index keeps the icon on the right column even after a
    fresh search repopulates the grid)."""

    filterIconClicked = Signal(int, QPoint)

    def __init__(self, filterable_columns, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._filterable_columns = set(filterable_columns)
        self._active_columns = set()
        theme_manager.theme_changed.connect(lambda _mode=None: self.viewport().update())

    def set_filter_active(self, logical_index, active):
        """Highlights (or un-highlights) the arrow to show a filter is
        currently applied on that column."""
        if active:
            self._active_columns.add(logical_index)
        else:
            self._active_columns.discard(logical_index)
        self.viewport().update()

    def _current_label(self, logical_index):
        model = self.model()
        if model is None:
            return None
        return model.headerData(logical_index, Qt.Orientation.Horizontal)

    def _icon_rect(self, section_rect):
        x = section_rect.right() - _ICON_SIZE - _ICON_MARGIN
        y = section_rect.center().y() - _ICON_SIZE // 2
        return QRect(x, y, _ICON_SIZE, _ICON_SIZE)

    def paintSection(self, painter, rect, logical_index):
        super().paintSection(painter, rect, logical_index)
        if self._current_label(logical_index) not in self._filterable_columns:
            return

        icon_rect = self._icon_rect(rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        active = logical_index in self._active_columns
        if active:
            color = QColor("#FF7A00")
        else:
            color = QColor(255, 255, 255) if theme_manager.mode == "dark" else QColor(60, 60, 60)

        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        # Simple downward chevron -- reads as "open filter", matches the
        # hand-drawn-lines approach already used for icons elsewhere in
        # this app (see Records.py's _HistoryItemDelegate) rather than
        # relying on a text glyph.
        cx, cy = icon_rect.center().x(), icon_rect.center().y()
        half_w = icon_rect.width() // 2
        top = cy - icon_rect.height() // 4
        bottom = cy + icon_rect.height() // 4
        painter.drawLine(cx - half_w, top, cx, bottom)
        painter.drawLine(cx, bottom, cx + half_w, top)

        painter.restore()

    def mousePressEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        logical_index = self.logicalIndexAt(pos)
        if logical_index >= 0 and self._current_label(logical_index) in self._filterable_columns:
            section_rect = QRect(
                self.sectionViewportPosition(logical_index), 0,
                self.sectionSize(logical_index), self.height(),
            )
            if self._icon_rect(section_rect).contains(pos):
                anchor = self.mapToGlobal(QPoint(section_rect.left(), self.height()))
                self.filterIconClicked.emit(logical_index, anchor)
                return  # swallow the click so it doesn't also trigger a resize/drag
        super().mousePressEvent(event)