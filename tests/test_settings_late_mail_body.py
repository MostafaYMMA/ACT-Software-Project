"""
Covers the Settings page's "Late Page Mail Template" section: the
QPlainTextEdit is seeded from ui.late_mail_body_settings.preset_body,
edits are persisted through it (debounced -- see
Settings.py::_on_late_mail_body_edited/_flush_late_mail_body), and the
helper text shows a live character count plus a truncation warning once
the text is long enough that some mail clients may silently cut off a
mailto: body.

Patches ui.Pages.Settings.late_mail_body_settings with a small stub (not
a real QSettings-backed singleton) so these tests never write into the
real ACTSoftware/TimecardApp store on this machine.

IMPORTANT: persistence is debounced behind a QTimer now (LATE_MAIL_BODY_
SAVE_DEBOUNCE_MS), not synchronous with textChanged. Every test that
types into the field explicitly flushes (page._flush_late_mail_body())
rather than waiting for the real timer, AND stops the timer afterward --
without the stop(), a still-pending QTimer would outlive the `with
patch(...)` block and, if some later test elsewhere in the same process
pumps the Qt event loop after the real 500ms has elapsed, fire against
the REAL (unpatched) late_mail_body_settings singleton and write test
text into the real store. This bit once already during development.
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
        self.addCleanup(page._late_mail_save_timer.stop)
        return page

    def _type_and_settle(self, page, stub, text):
        """Types `text`, deterministically flushes the debounced save (in
        place of waiting for the real timer), and stops the timer so it
        can never fire later against the real singleton once the patch
        context below has closed."""
        with patch("ui.Pages.Settings.late_mail_body_settings", stub):
            page._late_mail_body_edit.setPlainText(text)
            page._flush_late_mail_body()
            page._late_mail_save_timer.stop()

    def test_text_edit_is_seeded_from_the_stored_preset(self):
        stub = _StubLateMailBodySettings("Existing template text")
        page = self._build_page(stub)
        self.assertEqual(page._late_mail_body_edit.toPlainText(), "Existing template text")

    def test_typing_does_not_persist_until_the_debounced_save_fires(self):
        stub = _StubLateMailBodySettings("")
        page = self._build_page(stub)

        with patch("ui.Pages.Settings.late_mail_body_settings", stub):
            page._late_mail_body_edit.setPlainText("New template body")
            self.assertEqual(stub.calls, [], "should not persist synchronously on every keystroke")
            self.assertTrue(page._late_mail_save_timer.isActive())
            page._late_mail_save_timer.stop()

    def test_flushing_persists_through_the_settings_singleton(self):
        stub = _StubLateMailBodySettings("")
        page = self._build_page(stub)
        self._type_and_settle(page, stub, "New template body")

        self.assertEqual(stub.calls[-1], "New template body")
        self.assertEqual(stub.preset_body, "New template body")

    def test_hint_shows_a_character_count(self):
        stub = _StubLateMailBodySettings("")
        page = self._build_page(stub)
        self._type_and_settle(page, stub, "12345")

        self.assertIn("5 character", page._late_mail_body_hint.text())
        self.assertIn("Plain text only", page._late_mail_body_hint.text())

    def test_hint_warns_once_text_is_long_enough_to_risk_truncation(self):
        stub = _StubLateMailBodySettings("")
        page = self._build_page(stub)
        long_text = "x" * 2001
        self._type_and_settle(page, stub, long_text)

        self.assertIn("truncate", page._late_mail_body_hint.text().lower())

    def test_short_text_does_not_warn(self):
        stub = _StubLateMailBodySettings("")
        page = self._build_page(stub)
        self._type_and_settle(page, stub, "short")

        self.assertNotIn("truncate", page._late_mail_body_hint.text().lower())

    def test_focus_out_flushes_immediately_without_waiting_for_the_timer(self):
        from PySide6.QtCore import QEvent

        stub = _StubLateMailBodySettings("")
        page = self._build_page(stub)

        with patch("ui.Pages.Settings.late_mail_body_settings", stub):
            page._late_mail_body_edit.setPlainText("Typed then left the field")
            self.assertEqual(stub.calls, [])
            page.eventFilter(page._late_mail_body_edit, QEvent(QEvent.Type.FocusOut))
            self.assertEqual(stub.calls, ["Typed then left the field"])
            self.assertFalse(page._late_mail_save_timer.isActive())

    def test_hiding_the_page_flushes_immediately_without_waiting_for_the_timer(self):
        stub = _StubLateMailBodySettings("")
        page = self._build_page(stub)
        # hideEvent only fires on an actual visible->hidden transition --
        # a bare hide() on a widget that was never shown is a no-op.
        page.show()
        self.app.processEvents()

        with patch("ui.Pages.Settings.late_mail_body_settings", stub):
            page._late_mail_body_edit.setPlainText("Typed then navigated away")
            self.assertEqual(stub.calls, [])
            page.hide()
            self.assertEqual(stub.calls, ["Typed then navigated away"])
            self.assertFalse(page._late_mail_save_timer.isActive())


if __name__ == "__main__":
    unittest.main()
