"""
Central place for colors, fonts, and the global QSS stylesheet.
Change a color here and it updates everywhere - no hunting through page files.

NOTE on colors: approximate ACT-style white + orange palette. "More orange"
per latest request: sidebar is now solid orange (not just a light tint),
buttons are solid orange fills, avatars are solid orange. Exact brand hex
codes would need to come from ACT's actual CSS/brand guide if pixel-perfect
accuracy is ever required.
"""

COLOR_BG = "#FFFFFF"
COLOR_ACCENT = "#fc6a28"          # primary orange
COLOR_ACCENT_DARK = "#d9541a"     # hover/pressed states
COLOR_ACCENT_LIGHT = "#FFE3D2"    # soft tint, used sparingly (e.g. "add" tile)
COLOR_TEXT_PRIMARY = "#1A1A1A"
COLOR_TEXT_SECONDARY = "#6B6B66"
COLOR_TEXT_ON_ACCENT = "#FFFFFF"
COLOR_BORDER = "#F3D9C7"
COLOR_ERROR = "#C0392B"

FONT_FAMILY = "Segoe UI"

GLOBAL_STYLESHEET = f"""
QWidget {{
    font-family: '{FONT_FAMILY}';
}}

QLineEdit {{
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 8px;
    font-size: 13px;
    background: white;
}}
QLineEdit:focus {{
    border: 1px solid {COLOR_ACCENT};
}}

QPushButton#primaryButton {{
    background-color: {COLOR_ACCENT};
    color: {COLOR_TEXT_ON_ACCENT};
    border: none;
    border-radius: 6px;
    padding: 10px 16px;
    font-weight: 700;
    font-size: 13px;
}}
QPushButton#primaryButton:hover {{
    background-color: {COLOR_ACCENT_DARK};
}}
QPushButton#primaryButton:pressed {{
    background-color: {COLOR_ACCENT_DARK};
}}

QPushButton#secondaryButton {{
    background-color: white;
    color: {COLOR_ACCENT};
    border: 1px solid {COLOR_ACCENT};
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 13px;
}}
QPushButton#secondaryButton:hover {{
    background-color: {COLOR_ACCENT_LIGHT};
}}
"""