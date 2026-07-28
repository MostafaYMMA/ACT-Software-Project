"""
Regression test for a real Qt style-sheet-cascade bug: AccountCreationPage
used to set its own background via a bare, selector-less rule
(`apply_live_style(self, lambda c: f"background-color: {c['BG']};")`).
A selector-less rule set on an ancestor widget cascades its
background-color down through every descendant, winning over the
app-level `QPushButton#primaryButton` rule (ui/theme.py) for that
property - which silently painted the "Create Account" button the
page's own pale background instead of orange, making its (also light)
text unreadable in light mode. Fixed by scoping the rule to the page's
own class name instead of leaving it selector-less; this test paints
the real button and checks its actual pixel color, since "no exception
was raised" would not have caught this.
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class AccountPageButtonStylingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        from ui.theme_manager import theme_manager

        cls.app.setStyleSheet(theme_manager.stylesheet())

    def test_create_account_button_renders_the_accent_color_not_the_page_background(self):
        from PySide6.QtWidgets import QPushButton
        from ui.account_page import AccountCreationPage
        from ui.theme_manager import theme_manager

        page = AccountCreationPage()
        page.resize(400, 500)
        page.show()
        self.app.processEvents()

        btn = next(b for b in page.findChildren(QPushButton) if b.objectName() == "primaryButton")
        # Sample a corner, not the center - the center can land on glyph
        # pixels under a headless/offscreen font backend.
        pixel = btn.grab().toImage().pixelColor(5, 5)

        colors = theme_manager.colors()
        accent = _hex_to_rgb(colors["ACCENT"])
        bg = _hex_to_rgb(colors["BG"])

        self.assertLess(
            _distance((pixel.red(), pixel.green(), pixel.blue()), accent), 20,
            f"button should render the accent color {accent}, got "
            f"{(pixel.red(), pixel.green(), pixel.blue())} (page BG is {bg})",
        )
        page.deleteLater()


def _hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


if __name__ == "__main__":
    unittest.main()
