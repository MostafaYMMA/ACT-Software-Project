import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _QtTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _drain(self, ms):
        """Runs the Qt event loop for ms milliseconds so timers actually
        fire -- these animations are timer-driven, so without this nothing
        past the first step ever happens."""
        from PySide6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()


class RevealRowsTests(_QtTestCase):
    """reveal_rows hides rows and shows them back in batches. The rule that
    matters: every row must end up visible, in every path -- a bug here
    doesn't look like a broken animation, it looks like missing data."""

    def _table(self, row_count):
        from PySide6.QtWidgets import QTableWidget

        table = QTableWidget(row_count, 2)
        self.addCleanup(table.deleteLater)
        return table

    def test_all_rows_end_up_visible(self):
        from ui.transition import reveal_rows

        table = self._table(40)
        reveal_rows(table, total_ms=60, steps=6)
        self._drain(400)

        self.assertFalse(any(table.isRowHidden(row) for row in range(40)))

    def test_first_batch_is_visible_immediately(self):
        """Nothing should flash completely empty before the first timer tick."""
        from ui.transition import reveal_rows

        table = self._table(40)
        reveal_rows(table, total_ms=600, steps=6)

        self.assertFalse(table.isRowHidden(0))

    def test_an_empty_table_is_a_no_op(self):
        from ui.transition import reveal_rows

        table = self._table(0)
        reveal_rows(table)  # must not raise
        self.assertIsNone(getattr(table, "_row_reveal_timer", None))

    def test_a_restart_cancels_the_previous_reveal(self):
        """Switching tabs fast repopulates mid-reveal. Two live timers would
        fight over which rows are hidden and could strand some hidden."""
        from ui.transition import reveal_rows

        table = self._table(40)
        reveal_rows(table, total_ms=600, steps=6)
        first_timer = table._row_reveal_timer

        reveal_rows(table, total_ms=60, steps=6)
        self.assertIsNot(table._row_reveal_timer, first_timer)
        self.assertFalse(first_timer.isActive())

        self._drain(400)
        self.assertFalse(any(table.isRowHidden(row) for row in range(40)))

    def test_more_rows_do_not_take_longer(self):
        """The whole point of a fixed total: a 4000-row scan must not turn
        the reveal into a visible hang."""
        from ui.transition import reveal_rows

        table = self._table(4000)
        reveal_rows(table, total_ms=60, steps=6)
        self._drain(400)

        self.assertFalse(table.isRowHidden(3999))


class StaggerFadeInTests(_QtTestCase):
    """The failure mode here is a widget left stuck at opacity 0 -- i.e.
    invisible -- so the test is that every widget ends up with its effect
    detached and fully opaque."""

    def _labels(self, count):
        from PySide6.QtWidgets import QLabel

        labels = []
        for index in range(count):
            label = QLabel(str(index))
            self.addCleanup(label.deleteLater)
            labels.append(label)
        return labels

    def test_every_widget_ends_fully_visible(self):
        from ui.transition import stagger_fade_in

        labels = self._labels(4)
        stagger_fade_in(labels, duration=40, gap_ms=10)
        self._drain(600)

        for label in labels:
            self.assertIsNone(
                label.graphicsEffect(),
                msg="a left-attached opacity effect stops the widget repainting",
            )

    def test_an_empty_list_is_a_no_op(self):
        from ui.transition import stagger_fade_in

        stagger_fade_in([])  # must not raise

    def test_restarting_mid_flight_cancels_the_previous_run(self):
        """Every page show restarts this. Attaching the new effect deletes
        the one the previous call's DELAYED animations still target, and
        each then fires against a dead object -- Qt logs 'Changing state of
        an animation without target' for each. Cancelling first is what
        stops that."""
        from ui.transition import stagger_fade_in

        labels = self._labels(4)
        stagger_fade_in(labels, duration=200, gap_ms=200)  # long gaps: still pending
        pending = [getattr(label, "_stagger_timer", None) for label in labels[1:]]
        self.assertTrue(any(timer is not None and timer.isActive() for timer in pending))

        stagger_fade_in(labels, duration=20, gap_ms=5)

        for timer in pending:
            if timer is not None:
                self.assertFalse(timer.isActive(), "a stale delayed start survived")

        self._drain(600)
        for label in labels:
            self.assertIsNone(label.graphicsEffect())

    def test_repeated_restarts_leave_every_widget_visible(self):
        """The failure this guards against isn't cosmetic: a widget left
        holding a cancelled effect sits at opacity 0, i.e. invisible."""
        from ui.transition import stagger_fade_in

        labels = self._labels(4)
        for _ in range(5):
            stagger_fade_in(labels, duration=40, gap_ms=30)

        self._drain(800)
        for label in labels:
            self.assertIsNone(label.graphicsEffect())


class ThemeToggleSyncTests(_QtTestCase):
    """The top-bar switch and the Settings switch are two ToggleSwitch
    instances over one theme_manager. Clicking either has to move both."""

    def setUp(self):
        from ui.theme_manager import theme_manager

        self._original = theme_manager.mode
        self.addCleanup(theme_manager.set_mode, self._original)

    def test_two_switches_track_one_shared_mode(self):
        from ui.theme_manager import theme_manager
        from ui.toggle_switch import ToggleSwitch

        theme_manager.set_mode("light")
        top_bar_switch = ToggleSwitch()
        settings_switch = ToggleSwitch()
        self.addCleanup(top_bar_switch.deleteLater)
        self.addCleanup(settings_switch.deleteLater)

        self.assertFalse(top_bar_switch._is_dark)
        self.assertFalse(settings_switch._is_dark)

        # Clicking one is a plain theme_manager.toggle() -- the other is
        # updated by the theme_changed signal, not by anything the clicked
        # switch knows about.
        theme_manager.toggle()

        self.assertTrue(top_bar_switch._is_dark)
        self.assertTrue(settings_switch._is_dark)
        self.assertEqual(theme_manager.mode, "dark")

    def test_toggling_back_moves_both_again(self):
        from ui.theme_manager import theme_manager
        from ui.toggle_switch import ToggleSwitch

        theme_manager.set_mode("dark")
        first = ToggleSwitch()
        second = ToggleSwitch()
        self.addCleanup(first.deleteLater)
        self.addCleanup(second.deleteLater)

        theme_manager.toggle()

        self.assertFalse(first._is_dark)
        self.assertFalse(second._is_dark)


class NavIconTests(_QtTestCase):
    """Every nav key in app.py must have an icon that actually renders --
    a missing key silently gives a blank button."""

    def test_every_nav_icon_renders(self):
        from ui.nav_button import render_icon

        for key in ("dashboard", "records", "current_sheet", "export", "late", "settings"):
            pixmap = render_icon(key, 22)
            self.assertFalse(pixmap.isNull(), msg=f"{key} icon failed to render")

    def test_current_sheet_has_its_own_hover_animation(self):
        """It used to fall through to the generic pop, which only scales --
        so a non-zero rotation is what proves it took its own branch.
        Held in a local (not deleteLater'd) for the whole test: the
        animations target the button itself, and tearing the C++ object
        down under a running QPropertyAnimation raises from inside Qt."""
        from ui.app import IconRailButton

        button = IconRailButton("current_sheet", "CurrentSheet")

        button.play_hover_animation()
        self._drain(500)  # animations are async -- nothing moves until the loop runs

        self.assertNotEqual(button.icon_rotation, 0.0)
        self.assertGreater(button.icon_scale, 1.0)

        button.play_leave_animation()
        self._drain(500)
        self.assertAlmostEqual(button.icon_rotation, 0.0, places=3)
        self.assertAlmostEqual(button.icon_scale, 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
