"""
Covers the optional preset email-body text for the Late page's Send Mail
button:
  - ui/late_mail_body_settings.py's singleton (persistence pattern, no-op
    on unchanged value, change signal) -- exercised against a fake
    QSettings so these tests never touch the real ACTSoftware/TimecardApp
    store on this machine.
  - ui/Pages/Late.py::_send_mail_for_row actually including the preset as
    the mailto: URL's "body" parameter, correctly percent-encoded
    (including newlines), and leaving the URL unchanged when unset --
    exercised by patching the late_mail_body_settings name Late.py
    imports, for the same reason.
"""

import os
import sys
import unittest
from unittest.mock import patch
from urllib.parse import quote

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeQSettings:
    """Minimal in-memory stand-in for QSettings -- enough of its surface
    (value/setValue) for LateMailBodySettings, with no disk/registry I/O."""

    def __init__(self, *_args, **_kwargs):
        self._store = {}

    def value(self, key, default=None, type=None):  # noqa: A002 (matches QSettings' own signature)
        return self._store.get(key, default)

    def setValue(self, key, value):
        self._store[key] = value


class LateMailBodySettingsTests(unittest.TestCase):
    def _make_settings(self):
        import ui.late_mail_body_settings as mod

        with patch.object(mod, "QSettings", _FakeQSettings):
            return mod.LateMailBodySettings()

    def test_defaults_to_empty_string(self):
        settings = self._make_settings()
        self.assertEqual(settings.preset_body, "")

    def test_set_preset_body_updates_the_property_and_persists(self):
        settings = self._make_settings()
        settings.set_preset_body("Hi,\n\nJust checking in.")
        self.assertEqual(settings.preset_body, "Hi,\n\nJust checking in.")
        self.assertEqual(settings._settings.value("late_mail_preset_body", ""), "Hi,\n\nJust checking in.")

    def test_set_preset_body_emits_change_signal(self):
        settings = self._make_settings()
        seen = []
        settings.settings_changed.connect(lambda: seen.append(True))
        settings.set_preset_body("New text")
        self.assertEqual(len(seen), 1)

    def test_setting_the_same_value_again_does_not_re_emit(self):
        settings = self._make_settings()
        settings.set_preset_body("Same text")
        seen = []
        settings.settings_changed.connect(lambda: seen.append(True))
        settings.set_preset_body("Same text")
        self.assertEqual(len(seen), 0)

    def test_none_is_treated_as_empty(self):
        settings = self._make_settings()
        settings.set_preset_body("Something")
        settings.set_preset_body(None)
        self.assertEqual(settings.preset_body, "")


class _StubLateMailBodySettings:
    def __init__(self, preset_body=""):
        self.preset_body = preset_body


class LatePageMailtoBodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import storage_service  # noqa: F401

        self.get_stale_records_patcher = patch(
            "ui.Pages.Late.get_stale_records",
            return_value=[{
                "status": "Pending",
                "subject": "Fwd: 789",
                "Project Number": "555555555555",
                "Project Name": "HLGIU_NA_Marriott OPERA Cloud deployment",
                "Task Name": "Reporting - Reporting",
                "Date": "2026-06-01",
                "age_hours": 100,
                "sender": "sender@example.com",
            }],
        )
        self.get_stale_records_patcher.start()
        self.addCleanup(self.get_stale_records_patcher.stop)

    def _build_page(self):
        from ui.Pages.Late import LatePage

        page = LatePage()
        page.resize(1400, 300)
        page.show()
        self.app.processEvents()
        page._on_cell_entered(0, 3)
        return page

    def test_empty_preset_produces_the_same_url_as_no_body_at_all(self):
        page = self._build_page()
        with patch("ui.Pages.Late.late_mail_body_settings", _StubLateMailBodySettings("")):
            with patch("ui.Pages.Late.QDesktopServices.openUrl", return_value=True) as mock_open:
                page._on_send_mail_clicked()

        url = mock_open.call_args.args[0].toString()
        self.assertNotIn("body=", url)
        self.assertIn("mailto:sender%40example.com", url)
        self.assertIn("subject=", url)

    def test_preset_body_is_included_and_percent_encoded(self):
        from PySide6.QtCore import QUrl, QUrlQuery

        page = self._build_page()
        preset = "Hi team,\n\nAny update on this one?\n\nThanks"
        with patch("ui.Pages.Late.late_mail_body_settings", _StubLateMailBodySettings(preset)):
            with patch("ui.Pages.Late.QDesktopServices.openUrl", return_value=True) as mock_open:
                page._on_send_mail_clicked()

        qurl = mock_open.call_args.args[0]
        raw_url = qurl.toString()

        # A literal newline can't appear in a mailto: URL at all -- it
        # must carry the newline in its percent-encoded form.
        self.assertIn("%0A", raw_url)
        self.assertNotIn("\n", raw_url)

        # And round-tripping through Qt's own query parser recovers the
        # exact original text -- proving the encoding survives being
        # opened as a real mailto: URL, not just present as a substring.
        decoded_body = QUrlQuery(qurl).queryItemValue(
            "body", QUrl.ComponentFormattingOption.FullyDecoded
        )
        self.assertEqual(decoded_body, preset)

    def test_preset_body_does_not_disturb_recipient_or_subject(self):
        page = self._build_page()
        with patch("ui.Pages.Late.late_mail_body_settings", _StubLateMailBodySettings("Some body text")):
            with patch("ui.Pages.Late.QDesktopServices.openUrl", return_value=True) as mock_open:
                page._on_send_mail_clicked()

        url = mock_open.call_args.args[0].toString()
        self.assertTrue(url.startswith("mailto:sender%40example.com?subject="))


if __name__ == "__main__":
    unittest.main()
