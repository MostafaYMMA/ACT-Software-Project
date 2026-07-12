from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

from ui.theme_utils import apply_live_style


class PlaceholderPage(QWidget):
    """Generic 'not built yet' page, reused for Scan/Records/History/Settings
    until each gets real content. Once a section has real functionality,
    give it its own file instead of reusing this."""

    def __init__(self, title):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)

        title_label = QLabel(title)
        apply_live_style(title_label, lambda c: f"font-size: 18px; font-weight: 700; color: {c['TEXT_PRIMARY']};")
        layout.addWidget(title_label)

        subtitle = QLabel("This page is not built yet.")
        apply_live_style(subtitle, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 12px;")
        layout.addWidget(subtitle)

        layout.addStretch()