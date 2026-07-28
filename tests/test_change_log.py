"""
Covers the current_sheet change log: storage_service's change_log +
pending_change_baseline tables and their access points, plus the
read-only viewer (ui/change_log_dialog.ChangeLogDialog) the Current
Sheet's right-click menu opens.

Two behaviors are central here:
  - Local edits accumulate SILENTLY (pending_change_baseline) and only
    become a single change_log entry per field when an outgoing sync
    actually happens (storage_service.build_outgoing_snapshot's flush) --
    N edits in a row before a sync must produce exactly ONE log entry per
    field, from the value as of the last sync to the value at sync time.
  - The log reads oldest-first (ascending), both in the raw getters and
    in the dialog's table.
  - Incoming sync (apply_incoming_snapshot) is completely unaffected: it
    still logs immediately, one entry per changed field per payload --
    that side never touches pending_change_baseline.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage_service

SENDER_DEVICE = "aabbccdd1122"

_ENTRY = {
    "day": "2026-07-01",
    "labor_type": "Regular",
    "time_type": "Billable",
    "hours": "8",
    "project_code": "FB123",
    "project_name": "FB Test Project",
    "task": "Consulting",
    "name": "Omar",
    "person_number": "P1",
    "period": "2026-06-27 to 2026-07-03",
    "subject": "Weekly Card Approved",
    "sender": "someone@example.com",
    "received": "2026-07-02 09:00:00",
    "status": "Approved",
}


class ChangeLogBaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        db_path = os.path.join(self.temp_dir.name, "cards.db")
        self.db_patcher = patch.object(storage_service, "DB_PATH", db_path)
        self.db_patcher.start()
        self.addCleanup(self.db_patcher.stop)
        storage_service.init_db()

        storage_service.save_cards([dict(_ENTRY)])
        row = storage_service.get_current_sheet_rows()[0]
        self.cs_id = row["id"]
        self.timecard_id = row["timecard_id"]

    def _log(self):
        return storage_service.get_change_log_for_row(self.timecard_id)

    def _pending(self):
        conn = sqlite3.connect(storage_service.DB_PATH)
        try:
            return conn.execute(
                "SELECT timecard_id, field_name, baseline_value FROM pending_change_baseline"
            ).fetchall()
        finally:
            conn.close()

    def _sync(self, username="Mostafa"):
        """The moment an outgoing sync actually happens -- flushes
        whatever's pending in pending_change_baseline into change_log.
        Calling build_outgoing_snapshot directly (rather than going
        through push_updates/update_with_other_user) keeps these tests
        independent of Outlook/email plumbing; the flush lives inside
        build_outgoing_snapshot itself regardless of caller."""
        return storage_service.build_outgoing_snapshot(local_username=username)

    def _apply_incoming(self, seq=1, **overrides):
        incoming = dict(_ENTRY)
        incoming.update(overrides)
        return storage_service.apply_incoming_snapshot({
            "device_id": SENDER_DEVICE, "seq": seq, "kind": "snapshot",
            "rows": [incoming],
        })


class LocalEditAccumulationTests(ChangeLogBaseTests):
    """Local edits must not touch change_log at all until a sync flushes
    them -- that's the whole point of pending_change_baseline."""

    def test_seeding_a_row_is_not_itself_a_change(self):
        self.assertEqual(self._log(), [])
        self.assertEqual(self._pending(), [])

    def test_a_single_local_edit_creates_a_baseline_not_a_log_entry(self):
        self.assertTrue(storage_service.update_current_sheet_field(self.cs_id, "Qty", "10"))
        self.assertEqual(self._log(), [])
        self.assertEqual(self._pending(), [(self.timecard_id, "Qty", "8")])

    def test_the_baseline_captures_the_value_before_the_first_edit_only(self):
        """Four edits in a row: Blue -> Red -> Green -> Orange. The
        baseline must still point at the ORIGINAL (unset) colour, not at
        Blue, Red, or Green."""
        storage_service.set_current_sheet_row_color(self.cs_id, "#4A90D9")  # Blue
        storage_service.set_current_sheet_row_color(self.cs_id, "#E05252")  # Red
        storage_service.set_current_sheet_row_color(self.cs_id, "#4CAF50")  # Green
        storage_service.set_current_sheet_row_color(self.cs_id, "#FF7A00")  # Orange

        self.assertEqual(self._log(), [], "no change_log entries until a sync happens")
        self.assertEqual(self._pending(), [(self.timecard_id, "row_color", None)])

    def test_edits_to_different_fields_get_independent_baselines(self):
        storage_service.update_current_sheet_field(self.cs_id, "rate", 50)
        storage_service.update_current_sheet_field(self.cs_id, "rate", 100)
        storage_service.set_current_sheet_row_color(self.cs_id, "#4A90D9")
        storage_service.set_current_sheet_row_color(self.cs_id, "#E05252")

        pending = {field: baseline for _, field, baseline in self._pending()}
        self.assertEqual(pending, {"rate": "0.0", "row_color": None})

    def test_editing_a_field_back_to_its_original_value_still_leaves_a_baseline(self):
        """The baseline doesn't know the edit was undone -- that's resolved
        at flush time (see FlushOnSyncTests.test_editing_back_to_the_original_value_logs_nothing)."""
        storage_service.update_current_sheet_field(self.cs_id, "Qty", "10")
        storage_service.update_current_sheet_field(self.cs_id, "Qty", "8")  # back to original
        self.assertEqual(self._pending(), [(self.timecard_id, "Qty", "8")])
        self.assertEqual(self._log(), [])

    def test_refused_and_unknown_writes_neither_log_nor_create_a_baseline(self):
        self.assertFalse(storage_service.update_current_sheet_field(999999, "Qty", "1"))
        self.assertFalse(storage_service.update_current_sheet_field(self.cs_id, "id", "1"))
        self.assertFalse(storage_service.update_current_sheet_field(self.cs_id, "nope", "1"))
        self.assertFalse(storage_service.set_current_sheet_row_color(999999, "#FFF"))
        self.assertEqual(self._log(), [])
        self.assertEqual(self._pending(), [])

    def test_editing_does_not_corrupt_the_underlying_write(self):
        storage_service.update_current_sheet_field(self.cs_id, "Qty", "10")
        storage_service.update_current_sheet_field(self.cs_id, "rate", 100)
        storage_service.set_current_sheet_row_color(self.cs_id, "#FFD966")
        row = storage_service.get_current_sheet_rows()[0]
        self.assertEqual(str(row["Qty"]), "10")
        self.assertEqual(float(row["rate"]), 100.0)
        self.assertEqual(row["row_color"], "#FFD966")


class FlushOnSyncTests(ChangeLogBaseTests):
    """build_outgoing_snapshot's flush: pending baselines -> exactly one
    change_log entry per field, then the baseline rows are gone."""

    def test_four_color_edits_then_one_sync_produces_exactly_one_entry(self):
        storage_service.set_current_sheet_row_color(self.cs_id, "#4A90D9")  # Blue
        storage_service.set_current_sheet_row_color(self.cs_id, "#E05252")  # Red
        storage_service.set_current_sheet_row_color(self.cs_id, "#4CAF50")  # Green
        storage_service.set_current_sheet_row_color(self.cs_id, "#FF7A00")  # Orange

        self._sync()

        log = self._log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["field_name"], "row_color")
        self.assertIsNone(log[0]["old_value"])       # value before the FIRST edit
        self.assertEqual(log[0]["new_value"], "#FF7A00")  # value AT sync time
        self.assertEqual(self._pending(), [])

    def test_multiple_fields_each_flush_to_their_own_single_entry(self):
        storage_service.update_current_sheet_field(self.cs_id, "rate", 50)
        storage_service.update_current_sheet_field(self.cs_id, "rate", 100)
        storage_service.set_current_sheet_row_color(self.cs_id, "#4A90D9")
        storage_service.set_current_sheet_row_color(self.cs_id, "#E05252")

        self._sync()

        log = self._log()
        self.assertEqual(len(log), 2)
        by_field = {e["field_name"]: e for e in log}
        self.assertEqual(by_field["rate"]["old_value"], "0.0")
        self.assertEqual(by_field["rate"]["new_value"], "100.0")
        self.assertEqual(by_field["row_color"]["old_value"], None)
        self.assertEqual(by_field["row_color"]["new_value"], "#E05252")

    def test_flush_attributes_the_entry_to_the_local_username_passed_in(self):
        storage_service.update_current_sheet_field(self.cs_id, "Qty", "10")
        self._sync(username="Mostafa")
        self.assertEqual(self._log()[0]["source"], "Mostafa")

    def test_no_username_falls_back_to_local(self):
        storage_service.update_current_sheet_field(self.cs_id, "Qty", "10")
        storage_service.build_outgoing_snapshot()  # local_username omitted
        self.assertEqual(self._log()[0]["source"], "local")

    def test_editing_back_to_the_original_value_logs_nothing(self):
        storage_service.update_current_sheet_field(self.cs_id, "Qty", "10")
        storage_service.update_current_sheet_field(self.cs_id, "Qty", "8")  # back to original
        self._sync()
        self.assertEqual(self._log(), [])
        self.assertEqual(self._pending(), [], "the baseline is still consumed even with no net change")

    def test_a_sync_with_no_pending_edits_is_a_no_op(self):
        self._sync()
        self.assertEqual(self._log(), [])

    def test_editing_again_after_a_sync_starts_a_fresh_baseline(self):
        storage_service.set_current_sheet_row_color(self.cs_id, "#FF7A00")  # Orange
        self._sync()
        self.assertEqual(len(self._log()), 1)

        storage_service.set_current_sheet_row_color(self.cs_id, "#9B59B6")  # Purple
        self.assertEqual(len(self._log()), 1, "still just the first sync's entry")
        self._sync()

        log = self._log()
        self.assertEqual(len(log), 2)
        newest = log[-1]  # ascending order -- newest is last
        self.assertEqual(newest["old_value"], "#FF7A00")  # last flushed value, not the original
        self.assertEqual(newest["new_value"], "#9B59B6")

    def test_baseline_for_a_row_current_sheet_no_longer_has_is_dropped_silently(self):
        """A baseline can outlive its current_sheet row (e.g. Finalize
        deletes it before the closing snapshot's flush runs) -- must not
        crash, and produces no log entry since there's no current value
        to log a change TO."""
        storage_service.update_current_sheet_field(self.cs_id, "Qty", "10")
        conn = sqlite3.connect(storage_service.DB_PATH)
        conn.execute("DELETE FROM current_sheet WHERE id = ?", (self.cs_id,))
        conn.commit()
        conn.close()

        self._sync()  # must not raise
        self.assertEqual(self._log(), [])
        self.assertEqual(self._pending(), [])


class SyncAppliedLoggingTests(ChangeLogBaseTests):
    """apply_incoming_snapshot (the RECEIVING side) is explicitly
    untouched by this change -- it never involves pending_change_baseline
    and still logs immediately."""

    def test_incoming_change_is_logged_immediately_no_baseline_involved(self):
        result = self._apply_incoming(
            rate=250, rate_updated_at="2030-01-01 12:00:00", rate_updated_by=SENDER_DEVICE,
            row_color="#00FF00", color_updated_at="2030-01-01 12:00:00",
            color_updated_by=SENDER_DEVICE,
        )
        self.assertTrue(result["applied"])

        self.assertEqual(self._pending(), [])
        synced = [e for e in self._log() if e["source"] == SENDER_DEVICE]
        self.assertEqual({e["field_name"] for e in synced}, {"rate", "row_color"})
        for entry in synced:
            self.assertNotEqual(entry["old_value"], entry["new_value"])

    def test_newer_payload_carrying_unchanged_values_logs_nothing(self):
        common = dict(
            rate=250, rate_updated_by=SENDER_DEVICE,
            row_color="#00FF00", color_updated_by=SENDER_DEVICE,
        )
        self._apply_incoming(seq=1, rate_updated_at="2030-01-01 12:00:00",
                             color_updated_at="2030-01-01 12:00:00", **common)
        before = len(self._log())
        self._apply_incoming(seq=2, rate_updated_at="2030-06-01 12:00:00",
                             color_updated_at="2030-06-01 12:00:00", **common)
        self.assertEqual(len(self._log()), before)

    def test_local_pending_edit_and_remote_applied_edit_are_independent(self):
        # A local edit sits pending, untouched by an unrelated incoming sync.
        storage_service.update_current_sheet_field(self.cs_id, "Qty", "10")
        self._apply_incoming(
            rate=250, rate_updated_at="2030-01-01 12:00:00", rate_updated_by=SENDER_DEVICE,
        )
        self.assertEqual(self._pending(), [(self.timecard_id, "Qty", "8")])
        sources = [e["source"] for e in self._log() if e["field_name"] == "rate"]
        self.assertIn(SENDER_DEVICE, sources)
        self.assertEqual([e for e in self._log() if e["field_name"] == "Qty"], [])


class ChangeLogQueryOrderingTests(ChangeLogBaseTests):
    def test_get_change_log_for_row_is_oldest_first(self):
        for qty in ("10", "11", "12"):
            storage_service.update_current_sheet_field(self.cs_id, "Qty", qty)
            self._sync()  # one entry per sync, so three distinct entries to order

        ids = [e["id"] for e in self._log()]
        self.assertEqual(ids, sorted(ids))

    def test_get_recent_changes_returns_the_n_most_recent_oldest_first(self):
        values = []
        for qty in ("10", "11", "12", "13"):
            storage_service.update_current_sheet_field(self.cs_id, "Qty", qty)
            self._sync()
            values.append(qty)

        recent = storage_service.get_recent_changes(limit=2)
        self.assertEqual(len(recent), 2)
        # Must be the two MOST RECENT syncs (12->13, then 11->12)...
        self.assertEqual([e["new_value"] for e in recent], ["12", "13"])
        # ...but presented oldest-of-those-two-first.
        ids = [e["id"] for e in recent]
        self.assertEqual(ids, sorted(ids))

    def test_get_change_log_for_row_only_returns_that_row(self):
        storage_service.update_current_sheet_field(self.cs_id, "Qty", "10")
        self._sync()
        self.assertEqual(storage_service.get_change_log_for_row(999999), [])


class ChangeLogDialogTests(ChangeLogBaseTests):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _seed_one_local_and_one_remote(self):
        storage_service.update_current_sheet_field(self.cs_id, "rate", 100)
        storage_service.set_current_sheet_row_color(self.cs_id, "#FFD966")
        self._sync(username="Mostafa")
        self._apply_incoming(
            rate=250, rate_updated_at="2030-01-01 12:00:00", rate_updated_by=SENDER_DEVICE,
            row_color=None, color_updated_at="2030-01-01 12:00:00",
            color_updated_by=SENDER_DEVICE,
        )

    def _rendered(self, dialog):
        return [
            tuple(dialog.table.item(r, c).text() for c in range(dialog.table.columnCount()))
            for r in range(dialog.table.rowCount())
        ]

    def test_dialog_renders_every_entry_and_is_read_only(self):
        from PySide6.QtWidgets import QAbstractItemView
        from ui.change_log_dialog import ChangeLogDialog

        self._seed_one_local_and_one_remote()
        dialog = ChangeLogDialog(self.timecard_id, "a row")
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.table.rowCount(), len(self._log()))
        self.assertEqual(
            dialog.table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers
        )

    def test_table_reads_oldest_first_top_to_bottom(self):
        from ui.change_log_dialog import ChangeLogDialog

        self._seed_one_local_and_one_remote()
        dialog = ChangeLogDialog(self.timecard_id, "a row")
        self.addCleanup(dialog.deleteLater)

        timestamps = [row[0] for row in self._rendered(dialog)]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_local_and_remote_entries_are_labelled_differently(self):
        from ui.change_log_dialog import ChangeLogDialog

        self._seed_one_local_and_one_remote()
        dialog = ChangeLogDialog(self.timecard_id, "a row")
        self.addCleanup(dialog.deleteLater)

        sources = [row[4] for row in self._rendered(dialog)]
        self.assertIn("Mostafa", sources)
        self.assertTrue(any(SENDER_DEVICE in s for s in sources))

    def test_cleared_value_is_shown_as_an_explicit_placeholder(self):
        from ui.change_log_dialog import ChangeLogDialog

        storage_service.set_current_sheet_row_color(self.cs_id, "#FFD966")
        storage_service.set_current_sheet_row_color(self.cs_id, None)
        self._sync()
        dialog = ChangeLogDialog(self.timecard_id, "a row")
        self.addCleanup(dialog.deleteLater)

        # Only one entry (collapsed): (empty) -> (empty) never happens since
        # the baseline (unset, i.e. None) equals the final value (also
        # None) here -- use a value that ends up genuinely cleared instead.
        rendered = self._rendered(dialog)
        self.assertEqual(rendered, [], "unset -> set -> unset nets to no real change")

        # A genuine clear: set (and sync) first, THEN clear.
        storage_service.set_current_sheet_row_color(self.cs_id, "#FFD966")
        self._sync()
        storage_service.set_current_sheet_row_color(self.cs_id, None)
        self._sync()
        dialog2 = ChangeLogDialog(self.timecard_id, "a row")
        self.addCleanup(dialog2.deleteLater)
        rendered2 = self._rendered(dialog2)
        self.assertEqual(rendered2[-1][3], "(empty)")  # newest (the clear) is last

    def test_row_with_no_history_renders_empty_without_error(self):
        from ui.change_log_dialog import ChangeLogDialog

        dialog = ChangeLogDialog(999999, "nothing here")
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.table.rowCount(), 0)


class RealUsernameLoggingTests(ChangeLogBaseTests):
    """Covers threading the logged-in username through to change_log at
    flush time -- CurrentSheetPage.user_name -> UpdateWorker/FinalizeWorker
    -> update_with_other_user/finalize_month -> push_updates ->
    build_outgoing_snapshot(local_username=...)."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_page_stores_the_username_it_was_constructed_with(self):
        from ui.Pages.CurrentSheet import CurrentSheetPage

        page = CurrentSheetPage("Mostafa")
        self.addCleanup(page.deleteLater)
        self.assertEqual(page.user_name, "Mostafa")

    def test_page_with_no_username_defaults_to_none(self):
        from ui.Pages.CurrentSheet import CurrentSheetPage

        page = CurrentSheetPage()
        self.addCleanup(page.deleteLater)
        self.assertIsNone(page.user_name)

    def test_sync_button_passes_the_pages_username_to_update_worker(self):
        """Checked at the source-code level rather than driving the real
        click handler: that would start a genuine QThread around a mocked
        UpdateWorker whose finished/failed signals are also mocks, so the
        thread would never actually quit -- a leaked background thread for
        no benefit over reading what the handler constructs."""
        import inspect

        from ui.Pages.CurrentSheet import CurrentSheetPage

        source = inspect.getsource(CurrentSheetPage._on_sync_clicked)
        self.assertIn("local_username=self.user_name", source)

    def test_both_sync_and_finalize_pass_the_pages_username(self):
        """Both outgoing-push buttons on this page (Sync -> UpdateWorker,
        Finalize -> FinalizeWorker) must thread the username through --
        checked by counting both occurrences in the class source, since
        this class matching on just one wouldn't prove the other exists."""
        import inspect

        from ui.Pages.CurrentSheet import CurrentSheetPage

        source = inspect.getsource(CurrentSheetPage)
        self.assertEqual(source.count("local_username=self.user_name"), 2)

    def test_main_window_passes_its_username_to_current_sheet_page(self):
        """ui/app.py's MainWindow.__init__(user_name) must thread that
        same username into CurrentSheetPage's construction -- checked at
        the source-code level rather than building the real MainWindow,
        which drags in every other page's storage_service dependencies
        for no benefit here."""
        import inspect

        import ui.app as app_module

        source = inspect.getsource(app_module.MainWindow)
        self.assertIn("CurrentSheetPage(self.user_name)", source)

    def test_dialog_shows_the_real_username_plainly(self):
        from ui.change_log_dialog import ChangeLogDialog

        storage_service.update_current_sheet_field(self.cs_id, "Qty", "10")
        self._sync(username="Mostafa")

        dialog = ChangeLogDialog(self.timecard_id, "a row")
        self.addCleanup(dialog.deleteLater)
        sources = [
            dialog.table.item(r, 4).text() for r in range(dialog.table.rowCount())
        ]
        self.assertIn("Mostafa", sources)
        self.assertNotIn("This computer", sources)

    def test_a_username_that_happens_to_look_like_a_device_id_is_rare_but_not_crashed_on(self):
        """Documents the known, accepted edge case: _display_source
        distinguishes a local username from a device id purely by shape
        (12 lowercase hex characters -- see storage_service.get_device_id).
        A username that happens to match that shape exactly would be
        mislabeled "Other device (...)" -- rare in practice, and not
        something this fix needs to solve, but it must not crash."""
        from ui.change_log_dialog import ChangeLogDialog

        weird_username = "abcdef123456"  # 12 lowercase hex chars
        storage_service.update_current_sheet_field(self.cs_id, "Qty", "10")
        self._sync(username=weird_username)

        dialog = ChangeLogDialog(self.timecard_id, "a row")
        self.addCleanup(dialog.deleteLater)
        text = dialog.table.item(0, 4).text()
        self.assertIn(weird_username, text)  # shown somewhere, just mislabeled


class ColorNameDisplayTests(ChangeLogBaseTests):
    """Covers the Change History dialog showing a colour NAME instead of
    a raw hex code, for colour fields only -- the stored value itself is
    untouched (see LocalEditAccumulationTests.test_editing_does_not_corrupt_the_underlying_write)."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_row_color_shows_a_name_not_the_raw_hex(self):
        from ui.change_log_dialog import ChangeLogDialog

        # #9B59B6 is exactly HIGHLIGHT_PALETTE's "Purple" swatch -- the
        # colour every row_color value in practice actually is.
        storage_service.set_current_sheet_row_color(self.cs_id, "#9B59B6")
        self._sync()
        dialog = ChangeLogDialog(self.timecard_id, "a row")
        self.addCleanup(dialog.deleteLater)

        to_value = dialog.table.item(0, 3).text()
        self.assertEqual(to_value, "Purple")
        self.assertNotIn("#", to_value)

    def test_non_color_field_is_never_run_through_color_naming(self):
        from ui.change_log_dialog import ChangeLogDialog

        storage_service.update_current_sheet_field(self.cs_id, "rate", 100)
        self._sync()
        dialog = ChangeLogDialog(self.timecard_id, "a row")
        self.addCleanup(dialog.deleteLater)

        to_value = dialog.table.item(0, 3).text()
        self.assertEqual(to_value, "100.0")

    def test_cleared_color_still_shows_the_empty_placeholder_not_a_name(self):
        from ui.change_log_dialog import ChangeLogDialog

        storage_service.set_current_sheet_row_color(self.cs_id, "#FFD966")
        self._sync()
        storage_service.set_current_sheet_row_color(self.cs_id, None)
        self._sync()
        dialog = ChangeLogDialog(self.timecard_id, "a row")
        self.addCleanup(dialog.deleteLater)

        # Oldest first: the set is first, the clear is last.
        self.assertEqual(dialog.table.item(dialog.table.rowCount() - 1, 3).text(), "(empty)")

    def test_a_color_with_no_close_named_match_falls_back_to_raw_hex(self):
        from ui.change_log_dialog import ChangeLogDialog
        from ui.color_names import closest_color_name

        # Verified out-of-band to be far from all six HIGHLIGHT_PALETTE
        # colours (default threshold is 40) -- a self-check guards against
        # this drifting silently true if the named list or threshold ever
        # changes.
        candidate = "#4D03FE"
        assumption_holds = closest_color_name(candidate) is None
        if not assumption_holds:
            self.skipTest(f"{candidate} now resolves to a name -- pick a new probe colour")

        storage_service.set_current_sheet_row_color(self.cs_id, candidate)
        self._sync()
        dialog = ChangeLogDialog(self.timecard_id, "a row")
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.table.item(0, 3).text(), candidate)


class CurrentSheetChangeLogMenuTests(ChangeLogBaseTests):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _build_page(self):
        # The page mirrors the active export file rather than the raw scan
        # (CurrentSheetPage.refresh passes only_in_active_export=True), so
        # the row has to be in it before the grid will show anything.
        conn = sqlite3.connect(storage_service.DB_PATH)
        conn.execute(
            "INSERT OR REPLACE INTO active_export_rows "
            "(timecard_id, added_at, export_path, finalized) VALUES (?, ?, ?, 0)",
            (self.timecard_id, "2026-07-02 10:00:00", "dummy.xlsx"),
        )
        conn.commit()
        conn.close()

        from ui.project_type_settings import project_type_settings
        from ui.Pages.CurrentSheet import CurrentSheetPage

        # This is the app's REAL QSettings store, shared with the running
        # app -- put back whatever the user actually had selected.
        original_type = project_type_settings.project_type
        self.addCleanup(project_type_settings.set_project_type, original_type)
        project_type_settings.set_project_type("beverage")  # the FB* row seeded above
        page = CurrentSheetPage()
        page.refresh()
        self.addCleanup(page.deleteLater)
        return page

    def test_swatch_dropdown_stays_colours_only(self):
        """The leading column's dropdown is deliberately one highlight
        vocabulary -- the history entry belongs on right-click only."""
        page = self._build_page()
        self.assertTrue(page._displayed_rows)
        menu = page._build_palette_menu(0, page._displayed_rows[0])
        labels = [a.text().lower() for a in menu.actions()]
        self.assertFalse(any("history" in label for label in labels))

    def test_right_click_menu_offers_change_history(self):
        page = self._build_page()
        menu = page._build_row_menu(0, page._displayed_rows[0])
        labels = [a.text().lower() for a in menu.actions()]
        self.assertTrue(any("change history" in label for label in labels))

    def test_triggering_it_opens_the_dialog_on_that_rows_history(self):
        from ui.change_log_dialog import ChangeLogDialog

        storage_service.update_current_sheet_field(self.cs_id, "rate", 100)
        self._sync()
        page = self._build_page()
        action = next(
            a for a in page._build_row_menu(0, page._displayed_rows[0]).actions()
            if "change history" in a.text().lower()
        )

        opened = {}
        with patch.object(ChangeLogDialog, "exec",
                          lambda self: opened.setdefault("rows", self.table.rowCount())):
            action.trigger()
        self.assertEqual(opened.get("rows"), len(self._log()))
        self.assertEqual(opened.get("rows"), 1)

    def test_row_without_a_timecard_id_is_handled(self):
        page = self._build_page()
        page._show_change_history({"timecard_id": None})  # must not raise


if __name__ == "__main__":
    unittest.main()
