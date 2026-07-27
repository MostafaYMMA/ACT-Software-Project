"""
Covers ui/photo_lightbox.py's PhotoLightbox (the animated "shared
element" expand/collapse behind Avatar's "View photo" menu item) and
ui/avatar.py::_find_main_window, the helper that locates it.

Builds a minimal stand-in for ui.app.MainWindow -- a QWidget named
"mainWindow" containing a top-bar-like frame (with a real Avatar) and a
sidebar-like frame -- rather than constructing the real MainWindow, which
would drag in all six real pages and their storage_service/QSettings
dependencies for no benefit here: what's under test is purely the overlay
positioning/animation logic and _find_main_window's parent-walk, neither
of which cares what's actually inside "mainWindow" beyond its objectName
and geometry.
"""

import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class PhotoLightboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _pump(self, duration_ms):
        deadline = time.time() + duration_ms / 1000
        while time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

    def setUp(self):
        from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFrame
        from PySide6.QtGui import QPixmap, QColor
        from ui.avatar import Avatar

        self.main_window = QWidget()
        self.main_window.setObjectName("mainWindow")
        self.main_window.resize(900, 600)

        root = QVBoxLayout(self.main_window)
        root.setContentsMargins(0, 0, 0, 0)

        self.topbar = QFrame()
        self.topbar.setFixedHeight(68)
        self.topbar_layout = QHBoxLayout(self.topbar)
        self.avatar = Avatar("Test User", size=44)
        self.topbar_layout.addWidget(self.avatar)
        self.topbar_layout.addStretch()
        root.addWidget(self.topbar)

        body = QHBoxLayout()
        sidebar = QFrame()
        sidebar.setFixedWidth(56)
        body.addWidget(sidebar)
        content = QFrame()
        body.addWidget(content, stretch=1)
        root.addLayout(body)

        self.main_window.show()
        self.app.processEvents()

        pix = QPixmap(100, 100)
        pix.fill(QColor("red"))
        self.avatar._pixmap = pix

    def tearDown(self):
        self.main_window.deleteLater()
        self.app.processEvents()

    def test_find_main_window_resolves_to_the_named_ancestor_not_window(self):
        from ui.avatar import _find_main_window

        self.assertIs(_find_main_window(self.avatar), self.main_window)

    def test_scrim_covers_the_whole_window_including_sidebar_and_topbar(self):
        self.avatar._show_view_dialog()
        self.app.processEvents()
        lightbox = self.avatar._lightbox
        self.assertEqual(lightbox._scrim.geometry(), self.main_window.rect())

    def test_photo_starts_at_the_avatars_exact_position_and_size(self):
        avatar_rect = self.avatar.mapTo(self.main_window, self.avatar.rect().topLeft())
        self.avatar._show_view_dialog()
        self.app.processEvents()
        photo = self.avatar._lightbox._photo
        self.assertEqual(photo.geometry().topLeft(), avatar_rect)
        self.assertEqual(photo.geometry().size(), self.avatar.size())

    def test_stays_circular_and_moves_during_the_open_animation(self):
        from ui.photo_lightbox import ANIMATION_MS

        self.avatar._show_view_dialog()
        self.app.processEvents()
        lightbox = self.avatar._lightbox
        start_geo = lightbox._photo.geometry()

        self._pump(ANIMATION_MS * 0.5)
        mid_geo = lightbox._photo.geometry()

        self.assertEqual(mid_geo.width(), mid_geo.height(), "must stay perfectly circular mid-flight")
        self.assertGreater(mid_geo.width(), start_geo.width(), "should have grown by mid-flight")
        self.assertNotEqual(mid_geo.topLeft(), start_geo.topLeft(), "should be traveling, not resizing in place")
        self.assertTrue(0.0 < lightbox._scrim.dim_opacity < 1.0)

    def test_open_animation_ends_centered_at_the_expanded_diameter(self):
        from ui.photo_lightbox import ANIMATION_MS, EXPANDED_DIAMETER

        self.avatar._show_view_dialog()
        self.app.processEvents()
        lightbox = self.avatar._lightbox

        self._pump(ANIMATION_MS * 1.2)
        end_geo = lightbox._photo.geometry()

        self.assertEqual(end_geo.width(), EXPANDED_DIAMETER)
        self.assertEqual(end_geo.height(), EXPANDED_DIAMETER)
        self.assertAlmostEqual(end_geo.center().x(), self.main_window.rect().center().x(), delta=1)
        self.assertAlmostEqual(end_geo.center().y(), self.main_window.rect().center().y(), delta=1)
        self.assertAlmostEqual(lightbox._scrim.dim_opacity, 1.0, places=2)

    def test_scrim_tracks_a_window_resize_while_open(self):
        self.avatar._show_view_dialog()
        self.app.processEvents()
        lightbox = self.avatar._lightbox

        self.main_window.resize(1000, 640)
        self.app.processEvents()
        self.assertEqual(lightbox._scrim.geometry(), self.main_window.rect())

    def test_closing_targets_the_avatars_current_position_not_its_original_one(self):
        from ui.photo_lightbox import ANIMATION_MS

        self.avatar._show_view_dialog()
        self.app.processEvents()
        lightbox = self.avatar._lightbox
        self._pump(ANIMATION_MS * 1.2)  # let it fully open first

        old_avatar_rect = self.avatar.mapTo(self.main_window, self.avatar.rect().topLeft())
        self.topbar_layout.insertSpacing(0, 150)  # move the avatar via layout churn
        self.main_window.layout().activate()
        self.app.processEvents()
        new_avatar_rect = self.avatar.mapTo(self.main_window, self.avatar.rect().topLeft())
        self.assertNotEqual(new_avatar_rect, old_avatar_rect, "test setup didn't actually move the avatar")

        # Intercept cleanup so we can inspect the final geometry before
        # the widgets are torn down.
        cleanup_calls = []
        lightbox._cleanup = lambda: cleanup_calls.append(True)

        lightbox._on_scrim_clicked()
        self._pump(ANIMATION_MS * 1.2)

        final_geo = lightbox._photo.geometry()
        self.assertEqual(final_geo.topLeft(), new_avatar_rect)
        self.assertEqual(final_geo.size(), self.avatar.size())
        self.assertTrue(cleanup_calls)

    def test_dismiss_is_idempotent_against_a_second_scrim_click(self):
        from ui.photo_lightbox import ANIMATION_MS

        self.avatar._show_view_dialog()
        self.app.processEvents()
        lightbox = self.avatar._lightbox
        self._pump(ANIMATION_MS * 1.2)

        lightbox._on_scrim_clicked()
        lightbox._on_scrim_clicked()  # must not raise or start a second overlapping animation
        self._pump(ANIMATION_MS * 1.2)

    def test_cleanup_deletes_both_overlay_widgets(self):
        from ui.photo_lightbox import ANIMATION_MS

        self.avatar._show_view_dialog()
        self.app.processEvents()
        lightbox = self.avatar._lightbox
        self._pump(ANIMATION_MS * 1.2)

        scrim, photo = lightbox._scrim, lightbox._photo
        delete_calls = []
        scrim.deleteLater = lambda: delete_calls.append("scrim")
        photo.deleteLater = lambda: delete_calls.append("photo")

        lightbox._on_scrim_clicked()
        self._pump(ANIMATION_MS * 1.2)

        self.assertEqual(set(delete_calls), {"scrim", "photo"})

    def test_choose_and_remove_photo_are_unaffected(self):
        """Explicit non-goal check: the other two menu actions must still
        work exactly as before -- untouched by the lightbox change.
        Patches out the real QSettings write _remove_photo makes so this
        test never touches the real ACTSoftware/TimecardApp store."""
        from unittest.mock import patch

        with patch.object(self.avatar._settings, "setValue") as mock_set:
            self.avatar._remove_photo()
        mock_set.assert_called_once_with(self.avatar._settings_key(), "")
        self.assertIsNone(self.avatar._pixmap)


if __name__ == "__main__":
    unittest.main()
