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

FONT_FAMILY = "Segoe UI"

LIGHT = {
    "BG": "#FFFFFF",
    "SURFACE": "#F7F5F0",
    "ACCENT": "#fc6a28",
    "ACCENT_DARK": "#d9541a",
    "ACCENT_LIGHT": "#FFE3D2",
    "TEXT_PRIMARY": "#1A1A1A",
    "TEXT_SECONDARY": "#6B6B66",
    "TEXT_ON_ACCENT": "#FFFFFF",
    "BORDER": "#F3D9C7",
    "ERROR": "#C0392B",
}

DARK = {
    "BG": "#1E1E1E",
    "SURFACE": "#2A2A28",
    "ACCENT": "#fc6a28",
    "ACCENT_DARK": "#d9541a",
    "ACCENT_LIGHT": "#3A2A20",
    "TEXT_PRIMARY": "#F2F2F0",
    "TEXT_SECONDARY": "#B5B3AC",
    "TEXT_ON_ACCENT": "#FFFFFF",
    "BORDER": "#3A3A38",
    "ERROR": "#E57368",
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
    return f"""
QWidget {{
    font-family: '{FONT_FAMILY}';
}}

QLabel {{
    color: {colors['TEXT_PRIMARY']};
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

QPushButton#navButton {{
    text-align: left; padding: 12px 24px; border: none;
    color: {colors['TEXT_ON_ACCENT']}; background: transparent; font-size: 16px;
}}
QPushButton#navButton:hover {{
    background-color: {colors['ACCENT_DARK']};
}}

QLineEdit {{
    border: 1px solid {colors['BORDER']};
    border-radius: 6px;
    padding: 8px;
    font-size: 13px;
    background: {colors['SURFACE']};
    color: {colors['TEXT_PRIMARY']};
}}
QLineEdit:focus {{
    border: 1px solid {colors['ACCENT']};
}}

QPushButton#primaryButton {{
    background-color: {colors['ACCENT']};
    color: {colors['TEXT_ON_ACCENT']};
    border: none;
    border-radius: 6px;
    padding: 10px 16px;
    font-weight: 700;
    font-size: 13px;
}}
QPushButton#primaryButton:hover {{
    background-color: {colors['ACCENT_DARK']};
}}
QPushButton#primaryButton:pressed {{
    background-color: {colors['ACCENT_DARK']};
}}

QPushButton#secondaryButton {{
    background-color: {colors['SURFACE']};
    color: {colors['ACCENT']};
    border: 1px solid {colors['ACCENT']};
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 13px;
}}
QPushButton#secondaryButton:hover {{
    background-color: {colors['ACCENT_LIGHT']};
}}
"""


# Backward-compatible static stylesheet (light). main.py now applies the
# live version via theme_manager instead - this is kept only in case
# something else still imports it directly.
GLOBAL_STYLESHEET = build_stylesheet(LIGHT)