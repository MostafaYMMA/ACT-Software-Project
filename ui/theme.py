"""
Central place for colors, fonts, and the global QSS stylesheet.

Two full palettes now exist - LIGHT and DARK - see ui/theme_manager.py
for the singleton that decides which is active and persists the choice
across restarts.

The COLOR_* constants below always reflect LIGHT, kept only for backward
compatibility with files still doing `from ui.theme import COLOR_ACCENT`
directly (those won't re-color live when dark mode is toggled). New/updated
code should instead do:
    from ui.theme_manager import theme_manager
    colors = theme_manager.colors()
    colors["ACCENT"], colors["BG"], etc.
"""

import os

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

FONT_FAMILY = "'Segoe UI', 'SF Pro Text', 'Helvetica Neue', Arial, sans-serif"

# Fallback until the bundled font actually loads (see below) - if no
# font file is found, labels just render in a generic serif instead of
# crashing or silently doing nothing.
DISPLAY_FONT_FAMILY = "serif"

_font_load_attempted = False
_FONT_EXTENSIONS = (".ttf", ".otf")


def _ensure_display_font_loaded():
    """Loads whichever font file is sitting in assets/fonts/ - no fixed
    filename required. This is what makes "just drop in a different font
    file" actually work with zero code changes: whatever file is there
    (any name, .ttf or .otf) gets registered, and DISPLAY_FONT_FAMILY is
    read back out of its real internal family name.
    Deferred until a QApplication exists (QFontDatabase needs one) - if
    called too early (e.g. the GLOBAL_STYLESHEET line at the bottom of
    this file, evaluated at import time), it quietly no-ops and retries
    on the next call instead."""
    global _font_load_attempted, DISPLAY_FONT_FAMILY
    if _font_load_attempted:
        return
    if QApplication.instance() is None:
        return
    fonts_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "fonts",
    )
    if os.path.isdir(fonts_dir):
        for name in sorted(os.listdir(fonts_dir)):
            if name.lower().endswith(_FONT_EXTENSIONS):
                font_id = QFontDatabase.addApplicationFont(os.path.join(fonts_dir, name))
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    DISPLAY_FONT_FAMILY = f"'{families[0]}', serif"
                    break
    _font_load_attempted = True


# Shared corner-radius scale, used consistently instead of the previous
# ad-hoc mix of 6/8/10px values scattered through the stylesheet.
RADIUS_SM = 6
RADIUS_MD = 8
RADIUS_LG = 10

LIGHT = {
    "BG": "#FFFFFF",
    "SURFACE": "#F7F5F0",
    "SURFACE_ALT": "#FBFAF7",
    "ACCENT": "#FE5102",
    "ACCENT_DARK": "#DA4602",
    "ACCENT_LIGHT": "#FFDCCC",
    "ACCENT_SOFT": "#FFF0EA",
    "TEXT_PRIMARY": "#1A1A1A",
    "TEXT_SECONDARY": "#6B6B66",
    "TEXT_ON_ACCENT": "#FFFFFF",
    "BORDER": "#F3D9C7",
    "BORDER_STRONG": "#E8C4AA",
    "ERROR": "#C0392B",
    "DISABLED_BG": "#F0EEE9",
    "DISABLED_TEXT": "#B4B2AC",
    "SCROLLBAR_TRACK": "#F7F5F0",
    "SCROLLBAR_THUMB": "#E4DCD0",
    "SCROLLBAR_THUMB_HOVER": "#D8CBB8",
}

DARK = {
    "BG": "#000000",
    "SURFACE": "#0C0C0C",
    "SURFACE_ALT": "#070707",
    "ACCENT": "#FE5102",
    "ACCENT_DARK": "#DA4602",
    "ACCENT_LIGHT": "#3A2416",
    "ACCENT_SOFT": "#331F13",
    "TEXT_PRIMARY": "#F2F2F0",
    "TEXT_SECONDARY": "#B5B3AC",
    "TEXT_ON_ACCENT": "#FFFFFF",
    "BORDER": "#3A3A38",
    "BORDER_STRONG": "#4A4A46",
    "ERROR": "#E57368",
    "DISABLED_BG": "#242422",
    "DISABLED_TEXT": "#6E6C66",
    "SCROLLBAR_TRACK": "#2A2A28",
    "SCROLLBAR_THUMB": "#454540",
    "SCROLLBAR_THUMB_HOVER": "#54524B",
}

# Backward-compatible static (light) constants.
COLOR_BG = LIGHT["BG"]
COLOR_ACCENT = LIGHT["ACCENT"]
COLOR_ACCENT_DARK = LIGHT["ACCENT_DARK"]
COLOR_ACCENT_LIGHT = LIGHT["ACCENT_LIGHT"]
COLOR_TEXT_PRIMARY = LIGHT["TEXT_PRIMARY"]
COLOR_TEXT_SECONDARY = LIGHT["TEXT_SECONDARY"]
COLOR_TEXT_ON_ACCENT = LIGHT["TEXT_ON_ACCENT"]
COLOR_BORDER = LIGHT["BORDER"]
COLOR_ERROR = LIGHT["ERROR"]


def build_stylesheet(colors):
    """Builds the global QSS for a given palette dict (LIGHT or DARK).
    Anything styled through these selectors (objectName-based) recolors
    live the instant the app-level stylesheet changes - no rebuild needed."""
    _ensure_display_font_loaded()
    return f"""
QWidget {{
    font-family: {FONT_FAMILY};
    font-size: 13px;
}}

QLabel {{
    color: {colors['TEXT_PRIMARY']};
    font-family: {DISPLAY_FONT_FAMILY};
    font-style: italic;
}}

QToolTip {{
    background-color: {colors['TEXT_PRIMARY']};
    color: {colors['BG']};
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 6px 10px;
    font-size: 12px;
}}

#mainWindow {{
    background-color: {colors['BG']};
}}

#topBar {{
    background-color: {colors['SURFACE']};
}}

#sidebar {{
    background-color: {colors['ACCENT']};
    border: none;
}}

#navButton {{
    border: none;
    background: transparent;
}}
#navButton:hover {{
    background-color: {colors['ACCENT_DARK']};
}}
#navButton QLabel {{
    color: {colors['TEXT_ON_ACCENT']};
}}

/* ---------------------------------------------------------------- */
/* Inputs                                                            */
/* ---------------------------------------------------------------- */

QLineEdit {{
    border: 1px solid {colors['BORDER']};
    border-radius: {RADIUS_MD}px;
    padding: 9px 12px;
    font-size: 13px;
    background: {colors['SURFACE']};
    color: {colors['TEXT_PRIMARY']};
    selection-background-color: {colors['ACCENT_LIGHT']};
    selection-color: {colors['TEXT_PRIMARY']};
}}
QLineEdit:hover {{
    border: 1px solid {colors['BORDER_STRONG']};
}}
QLineEdit:focus {{
    border: 1.5px solid {colors['ACCENT']};
}}
QLineEdit:disabled {{
    background: {colors['DISABLED_BG']};
    color: {colors['DISABLED_TEXT']};
    border: 1px solid {colors['BORDER']};
}}

/* ---------------------------------------------------------------- */
/* Buttons                                                           */
/* ---------------------------------------------------------------- */

QPushButton#primaryButton {{
    background-color: {colors['ACCENT']};
    color: {colors['TEXT_ON_ACCENT']};
    border: none;
    border-radius: {RADIUS_MD}px;
    padding: 10px 18px;
    font-weight: 600;
    font-size: 13px;
}}
QPushButton#primaryButton:hover {{
    background-color: {colors['ACCENT_DARK']};
}}
QPushButton#primaryButton:pressed {{
    background-color: {colors['ACCENT_DARK']};
    padding-top: 11px;
    padding-bottom: 9px;
}}
QPushButton#primaryButton:disabled {{
    background-color: {colors['DISABLED_BG']};
    color: {colors['DISABLED_TEXT']};
}}

QPushButton#secondaryButton {{
    background-color: transparent;
    color: {colors['ACCENT']};
    border: 1.5px solid {colors['ACCENT']};
    border-radius: {RADIUS_MD}px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#secondaryButton:hover {{
    background-color: {colors['ACCENT_SOFT']};
}}
QPushButton#secondaryButton:pressed {{
    background-color: {colors['ACCENT_LIGHT']};
}}

QPushButton#periodToggle {{
    background-color: {colors['SURFACE']};
    color: {colors['TEXT_SECONDARY']};
    border: 1px solid {colors['BORDER']};
    border-radius: {RADIUS_MD}px;
    padding: 7px 14px;
    font-size: 12px;
    font-weight: 500;
}}
QPushButton#periodToggle:hover {{
    background-color: {colors['ACCENT_SOFT']};
    color: {colors['TEXT_PRIMARY']};
}}
QPushButton#periodToggle:checked {{
    background-color: {colors['ACCENT_SOFT']};
    color: {colors['ACCENT']};
    border: 1px solid {colors['ACCENT']};
    font-weight: 700;
}}

/* ---------------------------------------------------------------- */
/* Date picker                                                       */
/* ---------------------------------------------------------------- */

QDateEdit {{
    border: 1px solid {colors['BORDER']};
    border-radius: {RADIUS_MD}px;
    padding: 7px 10px;
    font-size: 12px;
    background: {colors['SURFACE']};
    color: {colors['TEXT_PRIMARY']};
}}
QDateEdit:hover {{
    border: 1px solid {colors['BORDER_STRONG']};
}}
QDateEdit:focus {{
    border: 1.5px solid {colors['ACCENT']};
}}
QDateEdit:disabled {{
    background: {colors['DISABLED_BG']};
    color: {colors['DISABLED_TEXT']};
    border: 1px solid {colors['BORDER']};
}}
QDateEdit::drop-down {{
    border: none;
    width: 22px;
}}
QDateEdit QAbstractItemView {{
    background: {colors['BG']};
    color: {colors['TEXT_PRIMARY']};
    border: 1px solid {colors['BORDER']};
    selection-background-color: {colors['ACCENT_LIGHT']};
    selection-color: {colors['TEXT_PRIMARY']};
    outline: none;
}}

/* ---------------------------------------------------------------- */
/* Tables                                                            */
/* ---------------------------------------------------------------- */

QTableWidget {{
    border: 1px solid {colors['BORDER']};
    border-radius: {RADIUS_MD}px;
    background: {colors['BG']};
    color: {colors['TEXT_PRIMARY']};
    gridline-color: {colors['BORDER']};
    alternate-background-color: {colors['SURFACE_ALT']};
    selection-background-color: {colors['ACCENT_SOFT']};
    selection-color: {colors['TEXT_PRIMARY']};
}}
QTableWidget::item {{
    color: {colors['TEXT_PRIMARY']};
    padding: 6px 8px;
    border: none;
}}
QTableWidget::item:selected {{
    background-color: {colors['ACCENT_SOFT']};
    color: {colors['TEXT_PRIMARY']};
}}
QHeaderView::section {{
    background-color: {colors['SURFACE']};
    color: {colors['TEXT_SECONDARY']};
    padding: 10px 8px;
    border: none;
    border-bottom: 1px solid {colors['BORDER']};
    font-weight: 700;
    font-size: 12px;
}}
QTableWidget QTableCornerButton::section {{
    background-color: {colors['SURFACE']};
    border: none;
}}

/* ---------------------------------------------------------------- */
/* Scrollbars                                                        */
/* ---------------------------------------------------------------- */

QScrollBar:vertical {{
    background: {colors['SCROLLBAR_TRACK']};
    width: 10px;
    margin: 0;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background: {colors['SCROLLBAR_THUMB']};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {colors['SCROLLBAR_THUMB_HOVER']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QScrollBar:horizontal {{
    background: {colors['SCROLLBAR_TRACK']};
    height: 10px;
    margin: 0;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
    background: {colors['SCROLLBAR_THUMB']};
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {colors['SCROLLBAR_THUMB_HOVER']};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}
"""


# Backward-compatible static stylesheet (light). main.py now applies the
# live version via theme_manager instead - this is kept only in case
# something else still imports it directly.
GLOBAL_STYLESHEET = build_stylesheet(LIGHT)