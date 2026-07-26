"""
Covers the Settings page's "Late Page Mail Template" section: the
QPlainTextEdit is seeded from ui.late_mail_body_settings.preset_body,
edits are persisted through it, and the helper text shows a live
character count plus a truncation warning once the text is long enough
that some mail clients may silently cut off a mailto: body.

Patches ui.Pages.Settings.late_mail_body_settings with a small stub
(not a real QSettings-backed singleton) so these tests never write into
the real ACTSoftware/TimecardApp store on this machine.
"""

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _StubLateMailBodySettings:
    def __init__(self, preset_body=""):
        self.preset_body = preset_body
        self.calls = []

    def set_preset_body(self, text):
        self.calls.append(text)
        self.preset_body = text


class SettingsLateMailBodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _build_page(self, stub):
        with patch("ui.Pages.Settings.late_mail_body_settings", stub):
            from ui.Pages.Settings import SettingsPage
            page = SettingsPage()
        return page

    def test_text_edit_is_seeded_from_the_stored_preset(self):
        stub = _StubLateMailBodySettings("Existing template text")
        page = self._build_page(stub)
        self.assertEqual(page._late_mail_body_edit.toPlainText(), "Existing template text")

    def test_typing_persists_through_the_settings_singleton(self):
        stub = _StubLateMailBodySettings("")
        page = self._build_page(stub)

        with patch("ui.Pages.Settings.late_mail_body_settings", stub):
            page._late_mail_body_edit.setPlainText("New template body")

        self.assertEqual(stub.calls[-1], "New template body")
        self.assertEqual(stub.preset_body, "New template body")

    def test_hint_shows_a_character_count(self):
        stub = _StubLateMailBodySettings("")
        page = self._build_page(stub)

        with patch("ui.Pages.Settings.late_mail_body_settings", stub):
            page._late_mail_body_edit.setPlainText("12345")

        self.assertIn("5 character", page._late_mail_body_hint.text())
        self.assertIn("Plain text only", page._late_mail_body_hint.text())

    def test_hint_warns_once_text_is_long_enough_to_risk_truncation(self):
        stub = _StubLateMailBodySettings("")
        page = self._build_page(stub)
        long_text = "x" * 2001

        with patch("ui.Pages.Settings.late_mail_body_settings", stub):
            page._late_mail_body_edit.setPlainText(long_text)

        self.assertIn("truncate", page._late_mail_body_hint.text().lower())

    def test_short_text_does_not_warn(self):
        stub = _StubLateMailBodySettings("")
        page = self._build_page(stub)

        with patch("ui.Pages.Settings.late_mail_body_settings", stub):
            page._late_mail_body_edit.setPlainText("short")

        self.assertNotIn("truncate", page._late_mail_body_hint.text().lower())


if __name__ == "__main__":
    unittest.main()
