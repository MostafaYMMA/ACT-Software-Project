"""
The Settings page's stale-threshold QSpinBox and QComboBox style their
::up-button/::down-button/::drop-down subcontrols, which makes Qt stop
drawing its own default arrow glyph on them (a well-known Qt QSS
behavior: once a stylesheet targets a subcontrol, the style's native
paint for it is suppressed unless the stylesheet supplies a replacement).
Without an explicit ::up-arrow/::down-arrow rule, that left plain blank
squares where the spin/dropdown arrows should be -- confirmed visually in
a screenshot of the running app.

Fixed with a plain-CSS border-triangle (the standard Qt QSS technique for
an arrow with no icon asset needed) on ::up-arrow/::down-arrow.
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class SettingsArrowGlyphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def test_spinbox_stylesheet_draws_up_and_down_arrows(self):
        from ui.Pages.Settings import SettingsPage

        page = SettingsPage()
        style = page._threshold_spin.styleSheet()
        self.assertIn("::up-arrow", style)
        self.assertIn("::down-arrow", style)
        # A plain empty subcontrol rule (just suppressing the button
        # background) is exactly the state that produced blank squares --
        # the fix has to actually draw something, not merely style the
        # button container.
        self.assertIn("border-bottom:", style)
        self.assertIn("border-top:", style)

    def test_combobox_stylesheet_draws_a_drop_down_arrow(self):
        from ui.Pages.Settings import SettingsPage

        page = SettingsPage()
        style = page._threshold_unit.styleSheet()
        self.assertIn("::down-arrow", style)
        self.assertIn("border-top:", style)


if __name__ == "__main__":
    unittest.main()
