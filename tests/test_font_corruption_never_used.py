"""
The bundled display font (assets/fonts/GentleHearts-Regular.ttf) is known
corrupted (see tests/test_font_assets.py) -- a text encode/decode round
trip replaced ~10,278 of its glyph-table bytes with U+FFFD. The assumption
had been that Qt simply refuses a font damaged this way and the app falls
back to a plain serif.

That assumption was wrong on at least one real Windows install: Qt
accepted the file (addApplicationFont did NOT return -1) and rendered text
using its damaged glyph outlines. Specific letters ('g' and 'y') came out
as the wrong shape, so real words silently became OTHER real words --
"Settings" -> "Settinas", "Light mode" -> "Liaht mode", "Sync" -> "Svnc",
"Days" -> "Davs", "pending"/"taking"/"waiting" -> "pendina"/"takina"/
"waitina" -- observed directly in a screenshot of the running Settings
page. Garbled-but-plausible words are worse than a generic serif fallback:
nothing about them looks broken, so they're easy to miss and easy to
misread as real content.

ui.theme._is_corrupted_font is the fix: the same byte-level corruption
signature test_font_assets.py already uses, checked BEFORE a font is ever
handed to Qt (both in ui/theme.py's own loader and in main.py's separate
diagnostic-only addApplicationFont call), so a corrupted file is never
trusted regardless of what any particular platform's font backend decides
to do with it.
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_FONT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "assets", "fonts", "GentleHearts-Regular.ttf"
)


class FontCorruptionDetectionTests(unittest.TestCase):
    def test_the_known_corrupted_font_is_detected(self):
        from ui.theme import _is_corrupted_font

        self.assertTrue(_is_corrupted_font(_FONT_PATH))

    def test_an_ordinary_clean_file_is_not_flagged(self):
        """The detection threshold (>=10 occurrences) must not fire on a
        handful of coincidental byte matches in a normal, uncorrupted
        file."""
        from ui.theme import _is_corrupted_font

        clean_path = os.path.join(os.path.dirname(__file__), "..", "main.py")
        self.assertFalse(_is_corrupted_font(clean_path))

    def test_a_missing_file_is_not_flagged_as_corrupted(self):
        from ui.theme import _is_corrupted_font

        self.assertFalse(_is_corrupted_font("/does/not/exist.ttf"))


class DisplayFontNeverUsesTheCorruptedFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_ensure_display_font_loaded_never_trusts_the_corrupted_family(self):
        import ui.theme as theme

        # Reset the module's own "already tried" latch so this test
        # exercises the real load path regardless of import order/earlier
        # tests in the same process.
        theme._font_load_attempted = False
        theme.DISPLAY_FONT_FAMILY = "serif"
        try:
            theme._ensure_display_font_loaded()
            self.assertNotIn(
                "GentleHearts", theme.DISPLAY_FONT_FAMILY,
                msg="the corrupted font's family name must never become DISPLAY_FONT_FAMILY",
            )
        finally:
            theme._font_load_attempted = False
            theme.DISPLAY_FONT_FAMILY = "serif"


if __name__ == "__main__":
    unittest.main()
